import asyncio
import time
import random
from pathlib import Path
import queue

from config import (
    BOT_TOKEN, ACCOUNTS, API_ID, API_HASH, MAX_PENDING_ITEMS,
    OLLAMA_MODEL, OLLAMA_BASE_URL, AI_REQUEST_INTERVAL, MAX_QUEUE_SIZE,
    LOGIN_MODE, AUTO_DISCOVER, BOT_PANEL_ENABLED, AUTOPILOT_DELAY,
)
from ai import generate_comment
from storage import PendingStore
from telegram_monitor import (
    AccountMonitor, create_user_client,
    monitor_backfill, monitor_rotation, monitor_inactive_channels, monitor_discovery,
)
from qrcode import QRCode
from datetime import datetime, timezone
from telethon.errors import SessionPasswordNeededError
from app_paths import account_dir
from desktop_bridge import DesktopBridge, consume_desktop_commands
from publisher import publish_comment


class QRWindow:
    """Отдельное окно QR, все Tk-операции выполняются в одном UI-потоке."""

    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("NeuroComment — Telegram QR Login")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.closed = False
        self.image_ref = None
        self.commands = queue.Queue()

        self.title_label = tk.Label(
            self.root, text="Вход в Telegram", font=("Segoe UI", 16, "bold"), pady=10
        )
        self.title_label.pack()
        self.image_label = tk.Label(self.root, padx=10, pady=5)
        self.image_label.pack()
        self.status_label = tk.Label(
            self.root, text="Ожидание сканирования…", font=("Segoe UI", 10), pady=8
        )
        self.status_label.pack()
        self.hint_label = tk.Label(
            self.root,
            text="Telegram → Настройки → Устройства → Подключить устройство",
            font=("Segoe UI", 9), pady=5,
        )
        self.hint_label.pack()
        self.root.after(50, self._pump)

    def _pump(self):
        try:
            while True:
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    break

                kind = command[0]
                if kind == "qr":
                    _, pil_image, expires_text = command
                    from PIL import ImageTk
                    self.image_ref = ImageTk.PhotoImage(pil_image)
                    self.image_label.configure(image=self.image_ref)
                    self.status_label.configure(text=f"QR-код действителен до {expires_text}")
                elif kind == "password":
                    _, done, holder = command
                    from tkinter import simpledialog
                    holder["password"] = simpledialog.askstring(
                        "Telegram 2FA",
                        "Введите пароль двухэтапной аутентификации:",
                        parent=self.root,
                        show="*",
                    )
                    done.set()
                elif kind == "close":
                    self.closed = True
                    self.root.destroy()
                    return

            if not self.closed:
                self.root.after(50, self._pump)
        except self.tk.TclError:
            self.closed = True

    def show_qr(self, pil_image, expires_text):
        if not self.closed:
            self.commands.put(("qr", pil_image, expires_text))

    def ask_password(self):
        if self.closed:
            return None
        import threading
        done = threading.Event()
        holder = {}
        self.commands.put(("password", done, holder))
        # Не зависаем навсегда, если QR-окно было закрыто в момент запроса 2FA.
        while not done.wait(0.2):
            if self.closed:
                return None
        return holder.get("password")

    def close(self):
        if not self.closed:
            self.commands.put(("close",))

    def _on_close(self):
        self.closed = True
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass


async def _is_really_authorized(client):
    try:
        me = await client.get_me()
        return me is not None
    except Exception:
        return False


async def _clean_restart(client):
    """Сохраняет сессию и переподключается в чистом состоянии после входа."""
    try:
        await client.session.save()
    except Exception:
        pass
    print("[LOGIN] Перезапускаю соединение в чистом состоянии...")
    try:
        await client.disconnect()
    except Exception:
        pass
    await asyncio.sleep(1)
    await client.connect()
    ok = await _is_really_authorized(client)
    print(f"[LOGIN] Соединение восстановлено. Авторизация: {'OK' if ok else 'ОШИБКА'}")


async def _finish_2fa(client, window=None):
    print("\nВход подтверждён, но на аккаунте стоит пароль (2FA).")
    if window is not None:
        pwd = await asyncio.to_thread(window.ask_password)
    else:
        pwd = await asyncio.to_thread(input, "Введите пароль: ")
    if not pwd:
        raise RuntimeError("Вход отменён: пароль 2FA не введён.")
    await client.sign_in(password=pwd.strip())
    return await _is_really_authorized(client)


async def _qr_login_attempt(client, window):
    qr = await client.qr_login()
    last_url = None
    attempts = 0
    while not await _is_really_authorized(client):
        if window.closed:
            print("QR-вход отменён пользователем.")
            return False
        attempts += 1
        if attempts > 20:
            print("QR-вход не удался за отведённое число попыток.")
            return False
        if qr.url != last_url:
            last_url = qr.url
            code = QRCode(border=4, box_size=8)
            code.add_data(qr.url)
            code.make(fit=True)
            img = code.make_image().convert("RGB")
            expires_text = qr.expires.strftime("%H:%M:%S") if qr.expires else "?"
            window.show_qr(img, expires_text)
            print(f"Новый QR-код, действует до {expires_text}.")
            print("→ Отсканируйте В ПРИЛОЖЕНИИ Telegram: Настройки → Устройства → "
                  "Подключить устройство, затем нажмите «Подтвердить» на телефоне.")
        try:
            remaining = (qr.expires - datetime.now(timezone.utc)).total_seconds()
        except Exception:
            remaining = 30
        remaining = max(1, min(int(remaining), 60))
        try:
            await asyncio.wait_for(qr.wait(), timeout=remaining)
            return True
        except SessionPasswordNeededError:
            return await _finish_2fa(client, window)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        except Exception as exc:
            print(f"[QR] Ошибка ожидания: {type(exc).__name__}: {exc}")
        if await _is_really_authorized(client):
            return True
        print("Срок действия QR-кода истёк — обновляю...")
        await asyncio.sleep(1)
        last_url = None
        try:
            await qr.recreate()
        except SessionPasswordNeededError:
            return await _finish_2fa(client, window)
        except Exception:
            qr = await client.qr_login()
            last_url = None
    return True


async def login_account(client, name):
    await client.connect()
    if await client.is_user_authorized():
        print(f"[{name}] Сессия активна — вход не требуется.")
        return
    if LOGIN_MODE == "phone":
        print(f"\n[{name}] Вход по номеру телефона. Формат: +79991234567")
        await client.start()
        await _clean_restart(client)
        return

    print(f"\n[{name}] Вход по QR-коду...")
    import threading
    window_ready = threading.Event()
    holder = {}

    def create_window():
        try:
            holder["window"] = QRWindow()
            window_ready.set()
            holder["window"].root.mainloop()
        except Exception as exc:
            holder["error"] = exc
            window_ready.set()

    thread = threading.Thread(target=create_window, daemon=True)
    thread.start()
    await asyncio.to_thread(window_ready.wait, 5)
    window = holder.get("window")

    if window is None:
        print(f"[{name}] Окно QR не открылось: {holder.get('error')}")
    else:
        try:
            ok = await _qr_login_attempt(client, window)
        finally:
            window.close()
            # Дожидаемся завершения потока окна, чтобы Tcl-ресурсы
            # освободились в своём потоке — без RuntimeWarning.
            await asyncio.to_thread(thread.join, 5)
        if ok:
            print(f"[{name}] Авторизован через QR.")
            await _clean_restart(client)
            return

    raise RuntimeError(
        f"[{name}] QR-вход не завершён. Запустите NeuroComment снова и отсканируйте новый QR-код."
    )


# ---- Desktop control / optional Telegram bot panel --------------------

panel = None
bridge = None
control_ready = None
local_autopilot_tasks = {}


def _cancel_local_autopilot(key):
    task = local_autopilot_tasks.pop(str(key), None)
    if task:
        task.cancel()


async def _local_autopublish_later(inst, key, delay):
    try:
        await asyncio.sleep(delay)
        item = await inst["store"].get(key)
        if item is None or not inst.get("autopilot"):
            return
        bridge.set_status(key, "Автопилот: публикация…")
        result = await publish_comment(inst, item)
        if result is True:
            await inst["store"].pop(key)
            bridge.remove(key)
            print(f"[{inst['name']}][AUTOPILOT-PC] Опубликовано: {key} — {item.chat_title}")
        elif result == "ACCOUNT_LIMITED":
            bridge.set_status(key, "Ограничение Telegram")
            print(f"[{inst['name']}][AUTOPILOT-PC] Ограничение Telegram, пост сохранён: {key}")
        else:
            await inst["store"].pop(key)
            bridge.remove(key)
            print(f"[{inst['name']}][AUTOPILOT-PC] Пост снят с очереди: {key} ({result})")
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        bridge.set_status(key, f"Ошибка: {type(exc).__name__}")
        print(f"[{inst['name']}][AUTOPILOT-PC] Ошибка {key}: {type(exc).__name__}: {exc}")
    finally:
        local_autopilot_tasks.pop(str(key), None)


def _schedule_local_autopilot(inst, item):
    key = str(item.key)
    _cancel_local_autopilot(key)
    delay = int(AUTOPILOT_DELAY * random.uniform(0.8, 1.2))
    local_autopilot_tasks[key] = asyncio.create_task(
        _local_autopublish_later(inst, key, delay)
    )
    bridge.set_status(key, f"Автопилот через ~{delay} с")


async def run_account(account):
    """Подготовка одного аккаунта: клиент, монитор, обработчики, задачи."""
    name = account["name"]
    workdir = str(account_dir(name))
    Path(workdir).mkdir(parents=True, exist_ok=True)

    store = PendingStore(MAX_PENDING_ITEMS)
    client = create_user_client(API_ID, API_HASH, account["session"], workdir)
    mon = AccountMonitor(client, workdir, name)
    inst = {
        "name": name,
        "client": client,
        "monitor": mon,
        "store": store,
        "autopilot": bool(account.get("autopilot", False)),
        "tasks": [],
    }

    ai_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    async def candidate(item=None, error=None):
        if error:
            print(f"[{name}] AI error:", error)
            return True
        if ai_queue.full():
            print(f"[{name}] Очередь заполнена — пост отложен: {item.key}")
            return False
        ai_queue.put_nowait(item)
        return True

    async def ai_worker():
        while True:
            item = await ai_queue.get()
            try:
                print(f"[{name}][AI] Обрабатываю {item.key} (в очереди: {ai_queue.qsize()})...")
                comment = await asyncio.to_thread(generate_comment, item.post_text)
                if comment.upper() == "SKIP":
                    # AI finished this post deliberately; only now persist it
                    # as processed. If AI had failed, it would remain retryable.
                    mon.complete_candidate(item.key, processed=True)
                    print(f"[{name}][AI] SKIP: {item.key}")
                    continue
                item.comment = comment
                await store.add(item)

                # PC is now the primary control surface. Candidate appears in
                # the desktop queue regardless of whether a bot is configured.
                bridge.upsert(item, autopilot=inst["autopilot"], status="Ожидает")
                # Candidate is now safely available to the user, so it may be
                # persisted as seen. Until this point a crash/error must not
                # make the post disappear forever.
                mon.complete_candidate(item.key, processed=True)
                print(f"[{name}][DESKTOP] Новый кандидат: {item.key}")

                # Wait until main() decides whether the optional Telegram bot
                # panel is actually available. This does not delay PC display.
                if control_ready is not None:
                    await control_ready.wait()
                if panel is not None:
                    t0 = time.time()
                    delivered = await panel.send_candidate(item)
                    if delivered:
                        print(f"[{name}][BOT] Кандидат отправлен ({time.time() - t0:.1f} c)")
                    elif inst["autopilot"]:
                        print(f"[{name}][BOT] BotPanel недоступна для кандидата — автопилот выполняет PC engine.")
                        _schedule_local_autopilot(inst, item)
                elif inst["autopilot"]:
                    _schedule_local_autopilot(inst, item)
            except Exception as exc:
                # Do not persist failed AI jobs as seen. Release the volatile
                # reservation so backfill can retry the post later.
                mon.complete_candidate(item.key, processed=False)
                print(f"[{name}] AI error: {exc}")
                try:
                    bridge.set_status(item.key, f"Ошибка AI: {type(exc).__name__}")
                except Exception:
                    pass
            finally:
                ai_queue.task_done()
                await asyncio.sleep(AI_REQUEST_INTERVAL)

    ai_task = asyncio.create_task(ai_worker())

    await login_account(client, name)
    await mon.leave_banned_channels()
    await mon.resolve_missing_channels()
    mon.register_handlers(candidate)

    tasks = [
        ai_task,
        asyncio.create_task(monitor_rotation(mon)),
        asyncio.create_task(monitor_backfill(mon, candidate)),
        asyncio.create_task(monitor_inactive_channels(mon)),
        asyncio.create_task(monitor_discovery(mon)),
    ]
    inst["tasks"] = tasks

    me = await client.get_me()
    print(f"[{name}] ✓ Авторизован: {me.first_name} — каналов: {len(mon.rotator.name_to_id)} — {mon.status()}")
    return inst


async def start_panel_with_retry(panel_obj, max_attempts=3, delay_sec=5):
    """Bot panel is optional: retry a few times, then fall back to PC-only."""
    for attempt in range(1, max_attempts + 1):
        try:
            await panel_obj.run()
            return True
        except Exception as exc:
            print(f"[BOT] Попытка {attempt}/{max_attempts} не удалась: {type(exc).__name__}: {exc}")
            try:
                await panel_obj.stop()
            except Exception:
                pass
            if attempt < max_attempts:
                await asyncio.sleep(delay_sec)
    return False


async def _find_pending(instances, key):
    for inst in instances.values():
        item = await inst["store"].get(key)
        if item is not None:
            return inst, item
    return None, None


async def desktop_command_loop(instances):
    """Execute actions requested from the Windows desktop UI."""
    while True:
        for command in consume_desktop_commands():
            action = str(command.get("action") or "").lower()
            key = str(command.get("key") or "")
            if not action or not key:
                continue

            inst, item = await _find_pending(instances, key)
            if item is None:
                bridge.remove(key)
                print(f"[DESKTOP] Команда {action}: {key} уже обработан.")
                continue

            try:
                if action == "skip":
                    _cancel_local_autopilot(key)
                    await inst["store"].pop(key)
                    bridge.remove(key)
                    if panel is not None:
                        await panel.sync_desktop_action("skip", item)
                    print(f"[{item.account}][DESKTOP] Пропущено: {key}")
                    continue

                if action == "edit":
                    new_comment = str(command.get("comment") or "").strip()
                    if not new_comment:
                        bridge.set_status(key, "Комментарий пуст")
                        continue
                    item.comment = new_comment
                    bridge.upsert(item, autopilot=inst["autopilot"], status="Ожидает")
                    if panel is not None:
                        await panel.sync_desktop_action("edit", item)
                    print(f"[{item.account}][DESKTOP] Комментарий изменён: {key}")
                    continue

                if action == "regen":
                    _cancel_local_autopilot(key)
                    bridge.set_status(key, "AI: новый вариант…")
                    new_comment = await asyncio.to_thread(generate_comment, item.post_text)
                    if new_comment.upper() == "SKIP":
                        bridge.set_status(key, "AI не предложил вариант")
                        if panel is None and inst["autopilot"]:
                            _schedule_local_autopilot(inst, item)
                        continue
                    item.comment = new_comment
                    bridge.upsert(item, autopilot=inst["autopilot"], status="Ожидает")
                    if panel is not None:
                        await panel.sync_desktop_action("regen", item)
                    elif inst["autopilot"]:
                        _schedule_local_autopilot(inst, item)
                    print(f"[{item.account}][DESKTOP] Новый AI-вариант: {key}")
                    continue

                if action == "approve":
                    _cancel_local_autopilot(key)
                    if panel is not None:
                        panel._cancel_autopilot(key)
                    edited_comment = str(command.get("comment") or "").strip()
                    if edited_comment:
                        item.comment = edited_comment
                        bridge.upsert(item, autopilot=inst["autopilot"], status="Ожидает")
                    bridge.set_status(key, "Публикация…")
                    result = await publish_comment(inst, item)
                    if result == "ACCOUNT_LIMITED":
                        bridge.set_status(key, "Ограничение Telegram")
                    else:
                        await inst["store"].pop(key)
                        bridge.remove(key)
                    if panel is not None:
                        await panel.sync_desktop_action("approve", item, result=result)
                    print(f"[{item.account}][DESKTOP] Публикация {key}: {result}")
                    continue

                print(f"[DESKTOP] Неизвестная команда: {action}")
            except Exception as exc:
                bridge.set_status(key, f"Ошибка: {type(exc).__name__}")
                print(f"[{item.account}][DESKTOP] Ошибка {action} {key}: {type(exc).__name__}: {exc}")

        await asyncio.sleep(0.35)


async def main():
    global panel, bridge, control_ready
    if not API_ID or not API_HASH:
        raise RuntimeError("Укажите Telegram API ID и API Hash в настройках NeuroComment.")
    if not ACCOUNTS:
        raise RuntimeError("Добавьте хотя бы один Telegram-аккаунт в NeuroComment Desktop.")

    bridge = DesktopBridge(clear_queue=True)
    control_ready = asyncio.Event()

    instances = {}
    all_tasks = []
    for account in ACCOUNTS:
        inst = await run_account(account)
        instances[inst["name"]] = inst
        all_tasks.extend(inst["tasks"])
        await asyncio.sleep(3)

    # Remote bot control is now optional. If it is disabled, missing a token,
    # or Telegram Bot API is temporarily unavailable, desktop control keeps
    # running normally.
    if BOT_PANEL_ENABLED and BOT_TOKEN:
        from bot_panel import BotPanel
        candidate_panel = BotPanel(instances, bridge=bridge)
        if await start_panel_with_retry(candidate_panel):
            panel = candidate_panel
            print("✓ Дополнительная Telegram BotPanel запущена.")
        else:
            panel = None
            print("⚠ Telegram BotPanel недоступна — продолжаю в режиме управления с ПК.")
    elif BOT_PANEL_ENABLED and not BOT_TOKEN:
        panel = None
        print("⚠ Telegram BotPanel включена, но BOT TOKEN пуст — продолжаю только с ПК.")
    else:
        panel = None
        print("✓ Telegram BotPanel выключена. Основное управление: ПК.")

    command_task = asyncio.create_task(desktop_command_loop(instances))
    all_tasks.append(command_task)
    control_ready.set()

    try:
        print(f"✓ Аккаунтов в работе: {len(instances)}")
        print("✓ PC-панель комментариев активна.")
        print("NeuroComment 1.1.4 Desktop engine запущен.")

        waiters = [
            asyncio.create_task(inst["client"].run_until_disconnected())
            for inst in instances.values()
        ]
        await asyncio.wait(waiters)
    except Exception as exc:
        print(f"[FATAL] NeuroComment остановлен: {type(exc).__name__}: {exc}")
        raise
    finally:
        for task in list(local_autopilot_tasks.values()):
            task.cancel()
        for task in all_tasks:
            task.cancel()
        for task in all_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if panel is not None:
            await panel.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 NeuroComment остановлен вручную (Ctrl+C). Все соединения закрыты корректно.")