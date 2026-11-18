# Hermes Image Persist

**Pasted images no longer vanish from the Hermes Desktop chat history — and the fix survives Hermes updates.**

Fix for [NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500) · Windows · Linux · macOS · Python 3.9+, stdlib only · MIT

Language / Язык / 语言 / भाषा: **[Русский](#русский)** · [English](#english) · [简体中文](#简体中文) · [हिन्दी](#हिन्दी)

---

<a id="русский"></a>

## Русский

Кроссплатформенный фикс (Windows / Linux / macOS) для бага
[NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500):

> В режимах Text/Auto (текстовый маршрут картинок) gateway сохраняет в историю
> служебный текст vision-обогащения вместо подписи пользователя + ссылок
> `@image:`, поэтому вставленные скриншоты пропадают из истории Desktop сразу
> после ответа модели и при каждой перезагрузке сеанса.

Проверен на **реальных исходниках** Hermes Agent **v0.23.1** (`a84025a`) и
**v0.23.2** (`ae2a81c`) — оба файла включены в репозиторий как фикстуры, и
патчер, и шим, и интеграционные проверки гоняются по ним (122 проверки,
`python selfcheck.py`).

### Что нового в 1.0.3 (почему 1.0.2 «встал», но снова не сработал)

Логи второго полевого отчёта показали ДВЕ вещи. Первая: **gateway-шлюз
переживает и перезапуск окна, и выход через трей** — после всех действий
ход в 01:43 обслуживало то же самое дерево процессов от 22:53 (нумерация
потоков 41 -> 53 непрерывна, а llama-server, убитый вручную, был
перезапущен в 01:43:05 самим старым шлюзом). Вторая: **мой флаг
--restart-gateway из 1.0.2 не работал** — PowerShell-запрос «найди процессы
с gateway.run/serve» находил сам себя (это видно прямо в вашей
диагностике), установщик гонялся за собственными копиями и зависал на
«Stopping...», не трогая настоящий шлюз.

1. **Классификация по таблице процессов.** Один CIM-запрос получает все
   процессы (pid/ppid/имя/cmdline/время старта), шлюз определяется в Python:
   токены командной строки, дети Desktop/Chromium исключены, зонды
   (powershell и т.п. с нашими же токенами) помечены pid=-1 и не могут
   быть выбраны — самоопределение структурно невозможно.
2. **Цепочка llama-server.** «Непрозрачный» шлюз определяется через его
   дочерний llama-server — ровно та улика, что была в ваших логах.
3. **Установщик теперь САМ останавливает живой шлюз** (по умолчанию;
   отказаться — --no-restart-gateway). После установки просто запустите
   Hermes — и фикс активен. Путь «установил → запустил → работает» стал
   поведением по умолчанию.
4. **Верификация standalone-питонов починена** (python.exe в корне
   tools/python-*, а не Scripts/) — вечное «no python in this
   environment / UNVERIFIED» исчезло.
5. 122 офлайн-проверки (было 111) + живая полевая симуляция.

### Что нового в 1.0.2 (найдена причина «всё стоит, но не работает»)

Логи с реальной машины показали: фикс 1.0.1 встал идеально (патчи
байт-в-байт, шим посажен и верифицирован), но **не сработал ни разу**.
Причина — **gateway-шлюз живёт дольше окна Desktop**: он запускается как
фоновый процесс `serve` и ПЕРЕЖИВАЕТ закрытие/открытие приложения. Gateway
стартовал в 22:53, фикс поставлен в 00:58 — ход в 00:59 обслуживал всё тот
же старый процесс со старым кодом в памяти. Python грузит код один раз при
старте процесса: ни `.pth`-шим, ни патч исходников не достучатся до уже
запущенного интерпретатора.

1. **Аудит gateway-процесса.** Установщик, гвард и `diagnose.py` теперь
   находят именно шлюз (`-m gateway.run` / `serve`), берут его время старта
   и сравнивают с временем установки фикса. Шлюз «до фикса» даёт статус
   `needs_gateway_restart` и громкое указание в диагностике.
2. **`--restart-gateway`.** `python install.py --apply --yes
   --restart-gateway` сам останавливает шлюз (мягко, с обработчиками
   завершения; llama-server перезапустится сам по требованию) — следующий
   старт Hermes уже будет с фиксом.
3. Без флага установщик честно предупреждает: «шлюз работает со старым
   кодом, фикс не подействует, пока он не перезапустится».

**Главное для пользователя:** перезапуск ОКНА Hermes недостаточен. Нужно
полностью выйти из Hermes (значок в трее → Exit) ИЛИ поставить с
`--restart-gateway`, либо завершить фоновые процессы Hermes в диспетчере
задач (НЕ трогая llama-server) и запустить Hermes снова.

### Что нового в 1.0.1 (починен отказ 1.0.0 на 0.23.2)

В 1.0.0 на некоторых установках 0.23.2 фикс ставился, но не действовал.
Причины были не в логике (она верна и проверена по реальным исходникам), а в
**доставке** — исправлены все три звена:

1. **Поиск окружения gateway стал надёжным.** `facts.json` читается из ВСЕХ
   `installs/*/` (PM мог сменить ключ состояния между версиями — тогда
   «committed»-venv становился невидим и шим сажался не туда). Плюс скан
   соседних `environments/` и, главное, — интерпретаторы **живых** процессов
   Hermes (командные строки Win32_Process / ps / /proc) как самые
   достоверные кандидаты.
2. **Верификация шима.** После посадки шим доказуемо импортируется и ставит
   свой хук из ПРИСВОЕННОГО окружения (`python -I`, как запускают шлюз).
   «Файл лежит, но .pth не исполняется» теперь видно сразу в установщике,
   гварде и `diagnose.py` (`VERIFIED` / `UNVERIFIED`).
3. **Source-патч больше не ждёт остановки Hermes.** Раньше при живом
   Desktop патч откладывался до бесконечности. Теперь ставится сразу:
   запись атомарная и проверяется компиляцией; работающий процесс держит
   старый модуль в памяти, новый старт подхватит патч. Требуется один
   перезапуск Hermes после установки.
4. Шим обёртывает и третий persist-шов (`run_turn`, queued follow-ups),
   поддерживает `file://`-пути и голые имена файлов из заметки,
   `diagnose.py` получил `--out <файл>` для полного отчёта одной командой.

### Как это работает — три независимых слоя

| Слой | Что делает | Переживает апдейт? |
|---|---|---|
| 1. Runtime shim | `hermes_image_persist.py` + строка `.pth`, подсаженные в site-packages того Python-окружения, в котором реально работает gateway (PM-окружение «committed generation», соседние generation-венвы, legacy in-tree venv). Бутстрап Hermes активирует это окружение через `site.addsitedir()`, который исполняет `.pth` в каждом процессе gateway/web/TUI — в том числе при запуске через `python -I`. Meta-path import hook оборачивает три точки (см. ниже). | Да — не заменяет ни один файл Hermes |
| 2. Source patch | Подстановка durable-текста после распаковки persist в `gateway/run_turn_prepare.py` (основные ходы) и после persist-строки follow-up в `gateway/run_turn.py` (очередь), плюс инертный helper `gateway/local_image_persist.py`. Адаптивные якоря: точный (проверен на 0.23.1 и 0.23.2) → толерантный к пробелам → структурный. Каждая запись проверяется компиляцией и бэкапится. | Нет — но переустанавливается слоем 3 |
| 3. Guard | Запись в планировщик ОС (Task Scheduler / LaunchAgent / systemd user timer / cron), запускающая `guard.py` раз в час и при входе в систему: пересаживает шим (например, когда апдейт создал новое dependency-поколение), переустанавливает source-патчи, когда Hermes остановлен, пишет статус. | Да |

### Что именно фиксится

Текстовый режим собирает для модели промпт вида
`[The user sent an image~ …описание… image_url: <путь> ~] + подпись`.
Для модели это корректно, но в историю это попадало как есть, а Desktop умеет
рисовать миниатюру только по строкам `@image:`. Фикс пересобирает **только
durable-проекцию** в `подпись + @image:-ссылки` — ровно ту форму, которую
сохраняет tui_gateway и которую возвращает апстримный фикс #142500. То, что
видит модель, не меняется никогда:

* `GatewayTurnPrepareMixin._hmwa_apply_message_timestamp` — persist-нога основного хода;
* `gateway.run_inbound.strip_discord_triggering_note` — тот же persist-шов для queued follow-up;
* `hermes_cli.web_routers.sessions._project_for_display` — display-проекция (`display_content`) для **старых строк**, уже сохранённых с прозой; база не переписывается.

Идемпотентно по построению: после первого же обмена текст перестаёт быть
note-подобным и остальные слои ничего не делают. Платформенно-независимо, как
и апстримный фикс; служебные (`internal`) ходы пропускаются. Native-режим,
веса, llama-server, config, ключи и SQLite-история не затрагиваются.

### Установка

Нужен установленный Hermes Agent 0.23.x (Desktop-бандл или git-checkout) и
любой Python 3.9+. Сети и pip-установок нет — только stdlib.

**Windows** — двойной клик по `install.bat`. Он сам найдёт Python (из
Hermes или системы), покажет предпросмотр и спросит `Y/N` перед установкой.
Также есть `diagnose.bat` (диагностика) и `selfcheck.bat` (122 проверки).

Если предпочитаете PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# сначала предпросмотр; затем добавить -Apply
```

**Linux / macOS**:

```bash
bash install.sh            # предпросмотр
bash install.sh --apply    # установка
```

или напрямую:

```bash
python3 install.py            # предпросмотр
python3 install.py --apply    # установка (попросит напечатать PATCH IMAGE PERSIST)
```

Установщик сначала печатает полный план (найденная версия, окружения, статус
якорей по файлам, регистрация guard) и чисто отказывается, если раскладка не
узнаётся. Флаги: `--root <путь>` / `--home <путь>` для нестандартных путей,
`--no-source`, `--no-shim`, `--no-guard`, `--yes`.

### Проверка

1. Новый сеанс, режим **Text**, вставить PNG с подписью, дождаться ответа →
   миниатюра остаётся; только подпись + картинка.
2. Переключиться на другой сеанс и вернуться → миниатюра на месте.
3. Повторить в **Auto**; **Native** должен работать как раньше.
4. В любой момент: `python diagnose.py` — слои, строки state.db (`IMG` = фикс
   работает, `NOTE` = старая проза), маркеры логов, REST-проекция, статус guard.
5. Маркер успеха в логах: `Image persistence: durable image references set`.

### Как переживаются обновления

* **Обновление исходников (0.23.2 → 0.23.3 …)**: слой 1 вообще не замечает
  подмены дерева; слой 3 в течение часа (или при следующем входе в систему)
  переустановит source-патчи, когда Hermes остановлен. Если якоря изменились
  до неузнаваемости, статус станет `needs_adaptation` — шим продолжает чинить
  новые ходы, — а `diagnose.py` напечатает, что прислать для адаптации.
* **Смена dependency-поколения (PM создал новый venv)**: guard сам пересадит
  шим в новое окружение.
* **Всё потеряно (чистая установка)**: повторить установщик.

### Откат

```bash
python rollback.py        # восстанавливает бэкапы, снимает шим и задачу планировщика
```

### Офлайн-проверка

```bash
python selfcheck.py
```

122 проверки: payload на реальных форматах заметок 0.23.1/0.23.2, патчер на
**реальных** исходниках апстрима (точный/пробельный/структурный якорь, CRLF,
замена блоков от более ранних сборок, идемпотентность, отказы), шим в
симулированном PM-окружении (`python -I` + `site.addsitedir` — ровно как в
`hermes_bootstrap`), интеграционный тест с **реальным `run_turn_prepare.py`
0.23.2** под шимом и полный жизненный цикл guard, включая симуляцию обновления.

### Структура репозитория

```
install.py                   универсальный установщик (предпросмотр / --apply)
install.bat                  двойной клик: автоустановка на Windows
install.ps1 / install.sh     платформенные обёртки (PowerShell / bash)
guard.py                     один проход обслуживания (точка входа планировщика)
diagnose.py                  диагностика только для чтения
diagnose.bat                 двойной клик: диагностика на Windows
rollback.py                  полное удаление
selfcheck.py                 офлайн-верификация (122 проверки)
selfcheck.bat                двойной клик: самопроверка на Windows
hermes_image_persist.py      runtime shim (подсаживается в site-packages)
payload/local_image_persist.py   helper для gateway/
hip/                         ядро: discovery, patcher, shim install, guard, schedulers
tests/fixtures/              реальные run_turn_prepare.py / run_turn.py апстрима (0.23.1, 0.23.2)
```

### Совместимость и благодарности

* Проверено на реальных исходниках: v0.23.1 (`a84025a`), v0.23.2 (`ae2a81c`).
  Движок якорей (точный → пробельный → структурный) рассчитан на минорные
  рефакторинги; неизвестная раскладка — отказ, а не угадывание.
* Апстрим: issue
  [#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
  (открыт), трекается в #134889; существует фикс-ветка сообщества
  (`potoior:fix/142500-enrichment-persist-refs-forkbase`). Когда апстрим
  примет свой фикс — удалите этот пакет.
* Фикстуры — немодифицированные фрагменты MIT-лицензированного
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
* Лицензия: MIT (см. `LICENSE`).

---

<a id="english"></a>

## English

Cross-platform (Windows / Linux / macOS) fix for
[NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500):

> In Text/Auto image-input modes the gateway persists the vision-enrichment
> prose instead of the user's caption + `@image:` refs, so pasted screenshots
> disappear from the Desktop history after the reply and on every reload.

Tested against the **real upstream sources** of Hermes Agent **v0.23.1**
(`a84025a`) and **v0.23.2** (`ae2a81c`) — both are shipped as test fixtures,
and the patcher, the shim and the integration checks all run against them
(122 checks, see `python selfcheck.py`).

### How it works — three independent layers

| Layer | What it does | Survives updates? |
|---|---|---|
| 1. Runtime shim | `hermes_image_persist.py` + a `.pth` line planted into the site-packages of the Python environment the gateway really runs on (the PM *committed generation* venv, sibling generations, legacy in-tree venvs). Hermes' own bootstrap activates that environment via `site.addsitedir()`, which executes `.pth` files in every gateway / web / TUI process — even with the `python -I` launchers. A meta-path import hook then wraps three seams (see below). | Yes — replaces no Hermes file |
| 2. Source patch | The durable-text swap inserted after the persist unpack in `gateway/run_turn_prepare.py` (main turns) and after the follow-up persist line in `gateway/run_turn.py` (queued turns), plus the inert helper `gateway/local_image_persist.py`. Adaptive anchors: exact (verified on 0.23.1 + 0.23.2) → whitespace-tolerant → structural. Every write is compile-checked and backed up. | No — but re-applied by layer 3 |
| 3. Guard | An OS scheduler entry (Task Scheduler / LaunchAgent / systemd user timer / cron) running `guard.py` hourly and at login: re-plants the shim (e.g. when an update created a new dependency generation), re-applies the source patches once Hermes is stopped, records status. | Yes |

### What the fix actually does

Text-mode image turns build the model prompt as
`[The user sent an image~ …description… image_url: <path> ~] + caption`.
That enrichment is correct for the model but was persisted verbatim as the
user row, and the Desktop can only rebuild a thumbnail from `@image:` lines.
The fix rebuilds **only the durable projection** as `caption + @image: refs`
(exactly the shape `tui_gateway` persists and upstream's own fix for #142500
restores) and never touches what the model sees:

* `GatewayTurnPrepareMixin._hmwa_apply_message_timestamp` — the persist leg of the main turn;
* `gateway.run_inbound.strip_discord_triggering_note` — the same persist seam used by queued follow-ups;
* `hermes_cli.web_routers.sessions._project_for_display` — display-only projection (`display_content`) for **old rows** already stored with the prose; the database is never rewritten.

Idempotent by design: once either seam swaps the text it is no longer
note-shaped, so every other layer becomes a no-op. Platform-agnostic like the
upstream fix; only `event.internal` turns are skipped. Native mode, weights,
llama-server, config, keys and SQLite history are never touched.

### Install

Requirements: Hermes Agent 0.23.x installed (Desktop bundle or git checkout)
and any Python 3.9+ (the one Hermes installed is fine). No network, no pip
installs — everything is stdlib.

**Windows** — double-click `install.bat`. It auto-finds Python (from Hermes
or the system), shows a preview, and asks `Y/N` before applying. There are
also `diagnose.bat` (diagnostics) and `selfcheck.bat` (122 checks).

If you prefer PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# preview first; then add -Apply
```

**Linux / macOS**:

```bash
bash install.sh            # preview
bash install.sh --apply    # install
```

or directly:

```bash
python3 install.py            # preview
python3 install.py --apply    # install (asks to type PATCH IMAGE PERSIST)
```

The installer prints a full plan (detected version, environments, anchor
status per file, guard registration) before changing anything, and refuses
cleanly when it does not recognize a layout.

Useful flags: `--root <path>` / `--home <path>` for non-default layouts,
`--no-source`, `--no-shim`, `--no-guard`, `--yes`.

### Verify

1. Fresh session, **Text** mode, paste a PNG with a caption, wait for the reply
   → the thumbnail stays; only caption + image remain.
2. Switch to another session and back → the thumbnail survives.
3. Repeat in **Auto**; **Native** must behave exactly as before.
4. Any time: `python diagnose.py` — layers, state.db rows (`IMG` = fixed,
   `NOTE` = old prose), log markers, REST projection, guard status.
5. Success marker in the logs:
   `Image persistence: durable image references set`.

### How updates are survived

* **Hermes source update (0.23.2 → 0.23.3 …)**: layer 1 never notices the
  tree change; layer 3 re-applies the source patches within an hour (or at
  next login) once Hermes is stopped. If the anchors changed beyond
  recognition, status becomes `needs_adaptation` — the shim still fixes new
  turns — and `diagnose.py` prints exactly what to send for a new anchor.
* **Dependency generation switch (PM creates a new venv)**: the guard
  re-plants the shim into the new committed environment automatically.
* **Everything lost (fresh install)**: re-run the installer.

### Rollback

```bash
python rollback.py        # restores backups, removes shim + scheduler entry
```

### Offline verification

```bash
python selfcheck.py
```

111 checks: payload against the real 0.23.1/0.23.2 enrichment-note formats,
the patcher against the **real** upstream sources (exact/whitespace/structural
anchors, CRLF, replacement of blocks from earlier builds, idempotency,
refusals), the shim in a simulated PM environment (`python -I` +
`site.addsitedir`, exactly like `hermes_bootstrap`), an integration test
importing the **real 0.23.2 `run_turn_prepare.py`** through the shim, and the
full guard lifecycle including a simulated update.

### Repository layout

```
install.py                   universal installer (preview / --apply)
install.bat                  double-click auto-installer for Windows
install.ps1 / install.sh     platform wrappers (PowerShell / bash)
guard.py                     one maintenance pass (scheduler entry point)
diagnose.py                  read-only diagnostics
diagnose.bat                 double-click diagnostics for Windows
rollback.py                  full removal
selfcheck.py                 offline verification suite (122 checks)
selfcheck.bat                double-click self-check for Windows
hermes_image_persist.py      the runtime shim (planted into site-packages)
payload/local_image_persist.py   helper planted into gateway/
hip/                         core: discovery, patcher, shim install, guard, schedulers
tests/fixtures/              real upstream run_turn_prepare.py / run_turn.py (0.23.1, 0.23.2)
```

### Compatibility & credits

* Verified against real sources: v0.23.1 (`a84025a`), v0.23.2 (`ae2a81c`).
  The anchor engine (exact → whitespace → structural) is built to keep
  adapting across minor refactors; unknown layouts fail closed.
* Upstream tracking: issue
  [#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
  (open), also tracked in #134889; a community fix branch exists
  (`potoior:fix/142500-enrichment-persist-refs-forkbase`). When upstream
  merges its own fix, uninstall this package.
* Test fixtures are unmodified excerpts of the MIT-licensed
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
* License: MIT (see `LICENSE`).

---

<a id="简体中文"></a>

## 简体中文

针对 [NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
的跨平台（Windows / Linux / macOS）修复：

> 在 Text/Auto（图片的文本路由）模式下，gateway 持久化到历史记录的是
> vision-增强的中间文本，而不是用户的说明文字 + `@image:` 引用，因此
> 粘贴的截图在模型回复后以及每次重新加载会话时都会从 Desktop 历史中消失。

已在 Hermes Agent **v0.23.1**（`a84025a`）与 **v0.23.2**（`ae2a81c`）的
**真实上游源码**上验证——两个版本的源码都作为测试固定件附带在仓库中，
补丁器、运行时 shim 和集成检查全部基于它们运行（122 项检查，
`python selfcheck.py`）。

### 工作原理——三个独立层

| 层 | 作用 | 能否在更新后保留？ |
|---|---|---|
| 1. 运行时 shim | `hermes_image_persist.py` + 一行 `.pth`，植入 gateway 实际运行的 Python 环境（PM 已提交 generation 的 venv、相邻 generation、旧版树内 venv）的 site-packages。Hermes 自身的引导通过 `site.addsitedir()` 激活该环境，从而在每个 gateway/web/TUI 进程中执行 `.pth`——即使启动器使用 `python -I`。随后由一个 meta-path 导入钩子包装三个接缝（见下）。 | 能——不替换任何 Hermes 文件 |
| 2. 源码补丁 | 在 `gateway/run_turn_prepare.py`（主回合）的持久化解包之后、以及 `gateway/run_turn.py`（排队 follow-up）的持久化行之后插入 durable 文本替换，外加惰性 helper `gateway/local_image_persist.py`。自适应锚点：精确（在 0.23.1 与 0.23.2 上验证）→ 容忍空白 → 结构化。每次写入都经过编译检查并备份。 | 不能——但由第 3 层重新应用 |
| 3. 守护（Guard） | 一条操作系统计划任务（Task Scheduler / LaunchAgent / systemd user timer / cron），每小时及登录时运行 `guard.py`：重新植入 shim（例如更新产生了新的依赖 generation）、在 Hermes 停止后重新应用源码补丁、记录状态。 | 能 |

### 修复的具体内容

Text 模式的图片回合会为模型构造形如
`[The user sent an image~ …描述… image_url: <路径> ~] + 说明文字` 的提示词。
这种增强文本对模型是正确的，但被原样持久化为用户消息行，而 Desktop
只能根据 `@image:` 行重建缩略图。本修复仅将**持久化投影**重建为
`说明文字 + @image: 引用`（与 `tui_gateway` 持久化的形式、以及上游对
#142500 的官方修复所恢复的形式完全一致），绝不改动模型看到的内容：

* `GatewayTurnPrepareMixin._hmwa_apply_message_timestamp` —— 主回合的持久化接缝；
* `gateway.run_inbound.strip_discord_triggering_note` —— 排队 follow-up 使用的同一持久化接缝；
* `hermes_cli.web_routers.sessions._project_for_display` —— 针对已存有增强文本**旧行**的只显示投影（`display_content`）；数据库永远不会被重写。

设计上幂等：任一接缝完成替换后，文本不再具有 note 形态，其余层自动变为
空操作。与上游修复一样平台无关；仅跳过 `event.internal` 回合。Native
模式、模型权重、llama-server、配置、密钥与 SQLite 历史绝不触碰。

### 安装

要求：已安装 Hermes Agent 0.23.x（Desktop 套装或 git checkout），任意
Python 3.9+（Hermes 自带的即可）。无需联网、无需 pip 安装——全部使用标准库。

**Windows** — 双击 `install.bat`。它会自动查找 Python（从 Hermes 或系统中），
显示预览，然后在应用前询问 `Y/N`。还提供 `diagnose.bat`（诊断）和
`selfcheck.bat`（122 项检查）。

如果偏好 PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# 先预览；确认后加 -Apply
```

**Linux / macOS**：

```bash
bash install.sh            # 预览
bash install.sh --apply    # 安装
```

或直接运行：

```bash
python3 install.py            # 预览
python3 install.py --apply    # 安装（需输入 PATCH IMAGE PERSIST 确认）
```

安装器在改动任何文件之前会打印完整计划（检测到的版本、环境、各文件
锚点状态、守护注册），遇到无法识别的布局会干净地拒绝。常用参数：
`--root <路径>` / `--home <路径>`（非默认布局）、`--no-source`、
`--no-shim`、`--no-guard`、`--yes`。

### 验证

1. 新建会话，**Text** 模式，粘贴一张 PNG 并附说明文字，等待回复 →
   缩略图保留；只剩说明文字 + 图片。
2. 切换到其他会话再切回 → 缩略图仍在。
3. 在 **Auto** 模式重复；**Native** 模式行为必须与之前完全一致。
4. 随时运行：`python diagnose.py` —— 各层状态、state.db 行（`IMG` = 修复
   生效，`NOTE` = 旧中间文本）、日志标记、REST 投影、守护状态。
5. 日志中的成功标记：`Image persistence: durable image references set`。

### 如何在更新后存活

* **Hermes 源码更新（0.23.2 → 0.23.3 …）**：第 1 层完全不受影响；第 3
  层会在一小时内（或下次登录时）、且 Hermes 已停止的情况下重新应用
  源码补丁。如果锚点变化到无法识别，状态会变为 `needs_adaptation`——
  shim 仍会修复新回合——`diagnose.py` 会精确打印需要提交什么来适配新锚点。
* **依赖 generation 切换（PM 创建了新 venv）**：守护会自动把 shim 重新
  植入新的已提交环境。
* **全部丢失（全新安装）**：重新运行安装器。

### 回滚

```bash
python rollback.py        # 恢复备份，移除 shim 与计划任务
```

### 离线验证

```bash
python selfcheck.py
```

111 项检查：payload 针对真实的 0.23.1/0.23.2 增强文本格式；补丁器针对
**真实**上游源码（精确/空白/结构化锚点、CRLF、早期构建遗留块的替换、
幂等、拒绝）；在模拟 PM 环境（`python -I` + `site.addsitedir`，与
`hermes_bootstrap` 一致）中的 shim；通过 shim 导入真实 0.23.2
`run_turn_prepare.py` 的集成测试；以及包含模拟更新在内的完整守护生命周期。

### 仓库结构

```
install.py                   通用安装器（预览 / --apply）
install.bat                  Windows 双击自动安装
guard.py                     一次维护扫描（计划任务入口）
diagnose.py                  只读诊断
diagnose.bat                 Windows 双击诊断
rollback.py                  完整卸载
selfcheck.py                 离线验证套件（122 项检查）
selfcheck.bat                Windows 双击自检
hermes_image_persist.py      运行时 shim（植入 site-packages）
payload/local_image_persist.py   植入 gateway/ 的 helper
hip/                         核心：发现、补丁器、shim 安装、守护、计划任务
tests/fixtures/              真实上游 run_turn_prepare.py / run_turn.py（0.23.1、0.23.2）
install.ps1 / install.sh     平台包装脚本
```

### 兼容性与致谢

* 已在真实源码上验证：v0.23.1（`a84025a`）、v0.23.2（`ae2a81c`）。锚点
  引擎（精确 → 空白 → 结构化）可跨小型重构持续适配；未知布局一律拒绝
  而非猜测。
* 上游跟踪：issue
  [#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
  （开放中），亦在 #134889 中跟踪；存在社区修复分支
  （`potoior:fix/142500-enrichment-persist-refs-forkbase`）。当上游合入
  官方修复后，请卸载本包。
* 测试固定件是 MIT 许可的
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
  的未修改摘录。
* 许可证：MIT（见 `LICENSE`）。

---

<a id="हिन्दी"></a>

## हिन्दी

[NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
के लिए क्रॉस-प्लेटफ़ॉर्म (Windows / Linux / macOS) फ़िक्स:

> Text/Auto (इमेज का टेक्स्ट मार्ग) मोड में gateway इतिहास में उपयोगकर्ता
> की कैप्शन + `@image:` रेफ़रेंस के बजाय vision-enrichment की सर्विस टेक्स्ट
> सहेजता है, इसलिए पेस्ट किए गए स्क्रीनशॉट उत्तर मिलने के तुरंत बाद और हर
> रीलोड पर Desktop के इतिहास से गायब हो जाते हैं।

Hermes Agent **v0.23.1** (`a84025a`) और **v0.23.2** (`ae2a81c`) के
**वास्तविक अपस्ट्रीम सोर्स** के साथ सत्यापित — दोनों संस्करण रिपॉज़िटरी
में टेस्ट फ़िक्स्चर के रूप में शामिल हैं, और पैचर, शिम तथा इंटीग्रेशन
जाँचें उन्हीं पर चलती हैं (122 जाँचें, `python selfcheck.py`)।

### यह कैसे काम करता है — तीन स्वतंत्र परतें

| परत | क्या करती है | अपडेट में बचती है? |
|---|---|---|
| 1. रनटाइम शिम | `hermes_image_persist.py` + एक `.pth` पंक्ति, जो उस Python वातावरण (PM की *committed generation* venv, सिबलिंग generation, पुरानी in-tree venv) के site-packages में रोपित की जाती है जिस पर gateway वास्तव में चलता है। Hermes का अपना बूटस्ट्रैप उस वातावरण को `site.addsitedir()` से सक्रिय करता है, जो हर gateway/web/TUI प्रक्रिया में `.pth` फ़ाइलें निष्पादित करता है — `python -I` लॉन्चर के साथ भी। एक meta-path इंपोर्ट हुक फिर तीन सीम को लपेटता है (नीचे देखें)। | हाँ — कोई Hermes फ़ाइल नहीं बदलती |
| 2. सोर्स पैच | `gateway/run_turn_prepare.py` (मुख्य टर्न) में persist अनपैक के बाद और `gateway/run_turn.py` (क्यूड फ़ॉलो-अप) में persist पंक्ति के बाद durable-टेक्स्ट अदला-बदली जोड़ी जाती है, साथ ही निष्क्रिय हेल्पर `gateway/local_image_persist.py`। अनुकूली एंकर: सटीक (0.23.1 + 0.23.2 पर सत्यापित) → व्हाइटस्पेस-सहिष्णु → संरचनात्मक। हर लेखन कंपाइल-जाँच और बैकअप से गुज़रता है। | नहीं — लेकिन परत 3 इसे दोबारा लगाती है |
| 3. गार्ड | OS शेड्यूलर प्रविष्टि (Task Scheduler / LaunchAgent / systemd user timer / cron) जो हर घंटे और लॉगिन पर `guard.py` चलाती है: शिम को दोबारा रोपित करता है (जैसे जब अपडेट ने नई dependency generation बनाई हो), Hermes रुकने पर सोर्स पैच दोबारा लगाता है, स्थिति दर्ज करता है। | हाँ |

### फ़िक्स वास्तव में क्या करता है

Text-मोड इमेज टर्न मॉडल के लिए प्रॉम्प्ट
`[The user sent an image~ …विवरण… image_url: <पथ> ~] + कैप्शन` के रूप में
बनाते हैं। यह enrichment मॉडल के लिए सही है, लेकिन वही टेक्स्ट यूज़र
पंक्ति के रूप में सहेजा जा रहा था, जबकि Desktop केवल `@image:` पंक्तियों
से ही थंबनेल बना सकता है। यह फ़िक्स केवल **durable प्रोजेक्शन** को
`कैप्शन + @image: रेफ़रेंस` के रूप में पुनर्निर्माण करता है (ठीक वही रूप जो
`tui_gateway` persist करता है और जिसे #142500 का अपस्ट्रीम फ़िक्स लौटाता
है) और मॉडल जो देखता है उसे कभी नहीं बदलता:

* `GatewayTurnPrepareMixin._hmwa_apply_message_timestamp` — मुख्य टर्न का persist सीम;
* `gateway.run_inbound.strip_discord_triggering_note` — क्यूड फ़ॉलो-अप द्वारा प्रयुक्त वही persist सीम;
* `hermes_cli.web_routers.sessions._project_for_display` — उन **पुरानी पंक्तियों** के लिए केवल-प्रदर्शन प्रोजेक्शन (`display_content`) जो पहले से सर्विस टेक्स्ट के साथ सहेजी गई हैं; डेटाबेस कभी दोबारा नहीं लिखा जाता।

डिज़ाइन से ही idempotent: कोई भी सीम टेक्स्ट बदल देने के बाद वह note-आकार
का नहीं रहता, इसलिए बाकी सभी परतें no-op हो जाती हैं। अपस्ट्रीम फ़िक्स की
तरह प्लेटफ़ॉर्म-अज्ञेय; केवल `event.internal` टर्न छोड़े जाते हैं। Native
मोड, मॉडल वेट, llama-server, config, कुंजियाँ और SQLite इतिहास कभी नहीं
छुए जाते।

### इंस्टॉल

आवश्यकताएँ: Hermes Agent 0.23.x (Desktop बंडल या git checkout) और कोई भी
Python 3.9+ (Hermes का अपना Python ठीक है)। नेटवर्क नहीं, pip इंस्टॉल नहीं —
सब कुछ stdlib।

**Windows** — `install.bat` पर डबल-क्लिक करें। यह Python (Hermes या सिस्टम से)
स्वतः ढूँढता है, प्रीव्यू दिखाता है, और लागू करने से पहले `Y/N` पूछता है।
`diagnose.bat` (डायग्नोस्टिक्स) और `selfcheck.bat` (122 जाँचें) भी उपलब्ध हैं।

यदि PowerShell पसंद हो:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# पहले प्रीव्यू; फिर -Apply जोड़ें
```

**Linux / macOS**:

```bash
bash install.sh            # प्रीव्यू
bash install.sh --apply    # इंस्टॉल
```

या सीधे:

```bash
python3 install.py            # प्रीव्यू
python3 install.py --apply    # इंस्टॉल (PATCH IMAGE PERSIST टाइप करने को कहेगा)
```

इंस्टॉलर कुछ भी बदलने से पहले पूरी योजना (पहचाना गया संस्करण, वातावरण,
प्रति फ़ाइल एंकर स्थिति, गार्ड पंजीकरण) छापता है, और अनजान लेआउट पर साफ़
इनकार करता है। उपयोगी फ़्लैग: ग़ैर-डिफ़ॉल्ट लेआउट के लिए `--root <path>` /
`--home <path>`, साथ ही `--no-source`, `--no-shim`, `--no-guard`, `--yes`।

### सत्यापन

1. नया सेशन, **Text** मोड, कैप्शन के साथ PNG पेस्ट करें, उत्तर का इंतज़ार
   करें → थंबनेल बना रहता है; केवल कैप्शन + इमेज रहती है।
2. दूसरे सेशन पर स्विच करके वापस आएँ → थंबनेल बचा रहता है।
3. **Auto** में दोहराएँ; **Native** पहले जैसा ही व्यवहार करे।
4. कभी भी: `python diagnose.py` — परतें, state.db पंक्तियाँ (`IMG` = फ़िक्स
   सक्रिय, `NOTE` = पुरानी सर्विस टेक्स्ट), लॉग मार्कर, REST प्रोजेक्शन,
   गार्ड स्थिति।
5. लॉग में सफलता मार्कर: `Image persistence: durable image references set`।

### अपडेट कैसे बचे जाते हैं

* **Hermes सोर्स अपडेट (0.23.2 → 0.23.3 …)**: परत 1 को ट्री बदलने का पता ही
  नहीं चलता; परत 3 एक घंटे के भीतर (या अगले लॉगिन पर), Hermes रुकने के
  बाद सोर्स पैच दोबारा लगा देती है। यदि एंकर पहचान से बाहर बदल गए तो
  स्थिति `needs_adaptation` हो जाती है — शिम नए टर्न ठीक करता रहता है —
  और `diagnose.py` बिल्कुल बताता है कि नया एंकर के लिए क्या भेजना है।
* **Dependency generation स्विच (PM ने नई venv बनाई)**: गार्ड नई committed
  वातावरण में शिम स्वतः दोबारा रोपित कर देता है।
* **सब खो गया (ताज़ा इंस्टॉल)**: इंस्टॉलर दोबारा चलाएँ।

### रोलबैक

```bash
python rollback.py        # बैकअप बहाल करता है, शिम + शेड्यूलर प्रविष्टि हटाता है
```

### ऑफ़लाइन सत्यापन

```bash
python selfcheck.py
```

122 जाँचें: वास्तविक 0.23.1/0.23.2 enrichment-note प्रारूपों पर payload;
**वास्तविक** अपस्ट्रीम सोर्स पर पैचर (सटीक/व्हाइटस्पेस/संरचनात्मक एंकर,
CRLF, पुराने बिल्ड के ब्लॉक की बदली, idempotency, इनकार); simulated PM
वातावरण (`python -I` + `site.addsitedir`, ठीक `hermes_bootstrap` जैसा) में
शिम; शिम के through वास्तविक 0.23.2 `run_turn_prepare.py` इंपोर्ट करने वाला
इंटीग्रेशन टेस्ट; और simulated अपडेट सहित पूरा गार्ड जीवनचक्र।

### रिपॉज़िटरी संरचना

```
install.py                   सार्वभौमिक इंस्टॉलर (प्रीव्यू / --apply)
install.bat                  Windows के लिए डबल-क्लिक ऑटो-इंस्टॉलर
guard.py                     एक रखरखाव पास (शेड्यूलर प्रवेश बिंदु)
diagnose.py                  केवल-पठन डायग्नोस्टिक्स
diagnose.bat                 Windows के लिए डबल-क्लिक डायग्नोस्टिक्स
rollback.py                  पूर्ण हटाना
selfcheck.py                 ऑफ़लाइन सत्यापन सूट (122 जाँचें)
selfcheck.bat                Windows के लिए डबल-क्लिक सेल्फ़-चेक
hermes_image_persist.py      रनटाइम शिम (site-packages में रोपित)
payload/local_image_persist.py   gateway/ में रोपित हेल्पर
hip/                         कोर: discovery, patcher, shim install, guard, schedulers
tests/fixtures/              वास्तविक अपस्ट्रीम run_turn_prepare.py / run_turn.py (0.23.1, 0.23.2)
install.ps1 / install.sh     प्लेटफ़ॉर्म रैपर
```

### संगतता और आभार

* वास्तविक सोर्स पर सत्यापित: v0.23.1 (`a84025a`), v0.23.2 (`ae2a81c`)।
  एंकर इंजन (सटीक → व्हाइटस्पेस → संरचनात्मक) छोटे रिफ़ैक्टर में भी अनुकूलित
  रहने के लिए बना है; अनजान लेआउट पर सुरक्षित इनकार, अंदाज़ा नहीं।
* अपस्ट्रीम ट्रैकिंग: issue
  [#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
  (खुला), #134889 में भी ट्रैक हो रहा है; समुदाय की फ़िक्स ब्रांच मौजूद है
  (`potoior:fix/142500-enrichment-persist-refs-forkbase`)। जब अपस्ट्रीम अपना
  फ़िक्स मर्ज कर ले, तो यह पैकेज हटा दें।
* टेस्ट फ़िक्स्चर MIT-लाइसेंस प्राप्त
  [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
  के अपरिवर्तित अंश हैं।
* लाइसेंस: MIT (`LICENSE` देखें)।
