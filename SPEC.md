# SPEC: edt-bridge — функционал Unica для проектов 1C:EDT

## 1. Назначение
MCP-сервер (stdio, Python/fastmcp) — прослойка между AI-агентом (OpenCode) и
связкой **EDT-MCP (HTTP :8765) + файловая система EDT-проекта + внешние CLI
(ring/1cedtcli/1cv8)**. Закрывает функциональные гэпы Unica, не покрытые
EDT-MCP (см. gaps.md). НЕ дублирует инструменты EDT-MCP: то, что EDT-MCP
умеет, вызывается напрямую.

Имя сервера: `edt-bridge`. Префикс инструментов: `edtb_`.

## 2. Конфигурация
Переменные окружения:
- `EDTB_PROJECT_PATH` — корень EDT-проекта (содержит .project, DT-INF/). Обязательна для файловых операций.
- `EDTB_EDT_MCP_URL` — URL EDT-MCP, default `http://localhost:8765/mcp`.
- `EDTB_ALLOW_FILE_MUTATIONS` — `1` разрешает пишущие файловые операции (default `0` = только план/preview).
- `EDTB_RING_CMD`, `EDTB_EDTCLI_CMD` — пути к ring/1cedtcli (для runtime-инструментов).

Каждый инструмент принимает опциональный `projectPath`, переопределяющий env.

## 3. Архитектура модулей
```
src/edt_bridge/
  server.py        # fastmcp bootstrap, регистрация инструментов
  config.py        # env + project discovery (валидация EDT-маркеров)
  safety.py        # git-checkpoint check, backup копии файлов, preview-diff
  proxy/client.py  # async MCP-клиент к EDT-MCP (Streamable HTTP), call_tool,
                   # degrade gracefully если недоступен (file-only режим, warning)
  proxy/resync.py  # после файловых правок: resync_to_disk + revalidate_objects
  mutations/       # пакеты мутаций (GAP-META-BATCH, dryRun/preview сквозное)
  forms/           # form_compile из JSON-DSL, form_info, form_remove (+pre-check)
  mxl/             # mxl_decompile, mxl_compile, mxl_info
  bsp/             # epf_scaffold (корневой .mdo + модули), help_add
  extensions/      # cfe_borrow_method (перехватчики), cfe_init_role
  interface/       # interface_edit (CommandInterface.xml), cf_panels
  runtime/         # designer_check (ring/1cedtcli), cf artifacts (обёртки)
```

## 4. Инструменты (контракты)

### 4.1 mutations — `mutations.py`
- `edtb_plan_mutations(ops: list[dict], projectPath?) -> plan`
  Валидация пакета операций БЕЗ применения. Для каждой op: проверка схемы,
  адресуемости цели (файл существует / EDT-MCP объект существует), оценка риска.
  Возвращает `{ok, ops: [{index, op, target, strategy: "edt-mcp"|"file", warnings}], hash}`.
- `edtb_apply_mutations(ops: list[dict], planHash: str, projectPath?) -> result`
  Применяет пакет. Требует planHash от свежего plan (защита от рассинхрона).
  Стратегии: `edt-mcp` — композиция create_metadata/modify_metadata/
  write_module_source через proxy; `file` — правка .mdo/.bsl XML + resync.
  Откат при ошибке середины пакета: восстановление backup-файлов (file-стратегия)
  или компенсирующие вызовы где возможно; в отчёте — явный статус каждой op.
  Ops: `createMetadata`, `modifyMetadata`, `deleteMetadata`, `writeModule`,
  `patchFile` (xpath-сетка изменений XML-файла).

### 4.2 forms — `forms.py` + `form_dsl.py`
- `edtb_form_compile(objectName, formName?, dsl: dict, mode: "create"|"patch", projectPath?)`
  JSON-DSL (синтаксис как у unica.form.compile: elements/commands/params/
  conditionalAppearance) → генерация плана элементов → применение через
  create_metadata/modify_metadata (assignable-свойства) И/ИЛИ прямая генерация
  фрагментов Form.form XML (тонкие свойства: inputHint, titleLocation,
  pagesRepresentation, callType=ChangeAndValidate, условное оформление).
  Возвращает `{applied, viaEdtMcp: [...], viaFile: [...], warnings}`.
- `edtb_form_info(objectName, formName) -> {elements, commands, params, events}` —
  разбор Form.form из проекта (без скриншота, headless-friendly).
- `edtb_form_remove(objectName, formName)` — пре-чек BSL-ссылок
  (search_in_code по имени формы через proxy), затем delete; отчёт о ссылках.

### 4.3 mxl — `mxl.py` + `mxl_parser.py`
- `edtb_mxl_decompile(objectName, templateName, projectPath?) -> json`
  Парсер текстового формата .mxl → JSON-DSL (areas, cells, params, merges,
  formatting: font/border/color/fillType, pageSetup).
- `edtb_mxl_compile(objectName, templateName, dsl: dict, projectPath?)`
  JSON-DSL → либо payload modify_metadata (поддерживаемое подмножество),
  либо генерация .mxl-файла (полный DSL) + resync.
- `edtb_mxl_info(objectName, templateName) -> {areas, params, mergeCount, ...}`
  из файла, без UI-скриншота.

### 4.4 bsp — `bsp.py` + шаблоны `templates/`
- `edtb_epf_scaffold(name, kind: "ExternalDataProcessor"|"ExternalReport",
  withBspModules: bool, projectPath?)`
  Генерация корневого `<name>.mdo` внешней обработки/отчёта по шаблону
  (валидный EDT XML, producedTypes/containedObjects), ObjectModule.bsl со
  стабами BSP-экспорта (СведенияОВнешнейОбработке и т.д. при withBspModules),
  формы при необходимости. Затем resync. Это закрывает GAP-EPF-ROOT.
- `edtb_help_add(objectName, lang: str, html: str, updateForms: bool)` —
  Help/<lang>.html рядом с объектом + IncludeHelpInContents на формах
  (modify_metadata или файловая правка).

### 4.5 extensions — `extensions.py`
- `edtb_cfe_borrow_method(extProjectPath, baseObjectName, moduleType, methodName,
  mode: "Before"|"After"|"Instead"|"ChangeAndValidate", newName?)`
  Читает исходник метода из базового проекта (файл .bsl), генерирует
  перехватчик (&Перед/&После/&Вместо/&ИзменениеИКонтроль) и вставляет через
  write_module_source в расширение (или файлово + resync).
- `edtb_cfe_init_role(extProjectPath, roleName?)` — создать «основную роль»
  расширения (create_metadata Role, fallback — файловый .mdo роли).

### 4.6 interface — `interface.py`
- `edtb_interface_edit(subsystemFQN, ops: list[dict], projectPath?)`
  Операции над CommandInterface.xml подсистемы: hide/show/place/order команд
  (commandsVisibility, порядок). Файловая правка XML + resync. plan/preview
  через mutations-механизм (patchFile ops).
- `edtb_cf_panels(ops: dict, projectPath?)` — секции интерфейса в
  Configuration.mdo: ClientApplicationInterface (панели Taxi),
  HomePageWorkArea (начальная страница). Файловая правка + resync.

### 4.7 runtime — `runtime.py`
- `edtb_designer_check(scope: "project"|"extension", projectPath?)` —
  запуск ring edt validate или 1cedtcli syntax-check, парсинг вывода в
  структуру {errors, warnings}. Graceful: если CLI нет — понятная ошибка
  с инструкцией.
- `edtb_cf_artifact(action: "dump"|"load", path, projectPath?)` — обёртки
  1cv8 DESIGNER / ring для .cf/.cfe.

### 4.8 сознательно НЕ реализуется (честные заглушки не нужны — документируем в README)
- Состояние поддержки (ParentConfigurations.bin) — формат недокументирован,
  обход: расширение/cfe-flow (уже описано в навыках).
- Скриншоты форм/макетов headless — требуют UI workbench.

## 5. Безопасность (обязательно)
1. Файловые мутации только при EDTB_ALLOW_FILE_MUTATIONS=1.
2. Перед пакетом мутаций: проверка `git status` в проекте — warning если dirty.
3. Перед правкой файла — backup в `.edtb-backup/<timestamp>/`.
4. После файловых правок — resync_to_disk + revalidate_objects через proxy;
   если EDT-MCP недоступен — warning «выполните resync вручную».
5. Все пишущие инструменты поддерживают `dryRun: true` → возврат плана/diff
   без применения.
6. Удаление — двухфазное (confirm token), как в EDT-MCP.

## 6. Тесты
Unit-тесты без живого EDT: парсеры (mxl, form.form-фрагменты, .mdo-шаблоны),
валидатор пакетов мутаций, DSL→план формы. Фикстуры в tests/fixtures/.
Интеграционные тесты (требуют EDT) — помечены skip по умолчанию.

## 7. Качество
- Русские docstring'и и сообщения об ошибках.
- Каждый инструмент возвращает структуру с полем `warnings: list[str]`.
- Никаких выдуманных инструментов EDT-MCP: proxy вызывает только имена из
  /mnt/agents/output/reference/edt-mcp-tools.md.
