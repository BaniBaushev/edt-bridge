# edt-bridge

MCP-сервер, который переносит функционал [Unica](https://github.com/IngvarConsulting/unica) на проекты в формате **1C:EDT**. Unica жёстко привязана к XML-формату конфигуратора (DESIGNER); edt-bridge реализует аналогичные возможности для EDT-формата как прослойку над [EDT-MCP](https://github.com/DitriXNew/EDT-MCP) плюс аккуратные файловые операции с `resync` обратно в EDT.

Это не форк Unica — кодовая база новая, LGPL-лицензия и runtime Unica не используются.

## Инструменты (18)

| Инструмент | Что делает | Аналог в Unica |
|---|---|---|
| `edtb_plan_mutations` | Валидация пакета мутаций, dryRun-план, planHash, confirmToken для удалений | пакетные операции |
| `edtb_apply_mutations` | Атомарное применение пакета: backup → apply → rollback при ошибке → resync | typed packages + dryRun |
| `edtb_form_compile` | Компиляция формы из JSON-DSL (create/patch, dryRun, пресеты dialog/list-with-filter/wizard) | form-compile |
| `edtb_form_info` | Headless-разбор Form.form → элементы/команды/параметры/события | form-info |
| `edtb_form_remove` | Двухфазное удаление формы с пре-чеком BSL-ссылок через `search_in_code` | form-remove |
| `edtb_mxl_decompile` | MXL (табличный документ) → JSON-DSL | mxl-decompile |
| `edtb_mxl_compile` | JSON-DSL → MXL | mxl-compile |
| `edtb_mxl_info` | Сводка по макету MXL | — |
| `edtb_epf_scaffold` | Скаффолдинг внешней обработки/отчёта по БСП (БСП-API, подсистемы) | epf-bsp-init |
| `edtb_help_add` | Добавление раздела справки в BSP-объект | help-add |
| `edtb_cfe_borrow_method` | Заимствование метода в расширение (CFE) с корректной обёрткой | cfe-borrow |
| `edtb_cfe_init_role` | Роль расширения: добавление прав в роль CFE | role-compile (CFE) |
| `edtb_interface_edit` | Правка командного интерфейса подсистемы (CommandInterface.xml) | command-interface |
| `edtb_interface_info` | Чтение командного интерфейса | — |
| `edtb_cf_panels` | Панели конфигурации / начальная страница (Configuration.mdo) | config-panels |
| `edtb_cf_panels_info` | Чтение панелей и home page | — |
| `edtb_designer_check` | Обёртка `1cv8 DESIGNER /CheckConfig` (через ring/1cedtcli) | designer-check |
| `edtb_cf_artifact` | Сборка cf/epf артефактов через внешние CLI | build-artifact |

## Архитектура

```
OpenCode / любой MCP-клиент
        │  stdio
        ▼
  edt-bridge (этот сервер)
        ├─ стратегия «edt-mcp»: JSON-RPC → EDT-MCP (http://localhost:8765/mcp)
        │     create_metadata / modify_metadata / delete_metadata /
        │     write_module_source / search_in_code / resync_to_disk /
        │     revalidate_objects / get_server_status
        └─ стратегия «file»: точечные lxml-патчи файлов EDT-проекта
              (только при EDTB_ALLOW_FILE_MUTATIONS=1, backup + resync)
```

Тонкие свойства форм (inputHint, titleLocation, pagesRepresentation, условное оформление, `ChangeAndValidate`) EDT-MCP не умеет — они идут файловой стратегией; остальное проходит через живую модель EDT.

## Установка

```bash
pip install -e .
# зависимости: fastmcp, httpx, lxml, pydantic
```

Подключение в OpenCode (`opencode.json`):

```json
{
  "mcp": {
    "edt-bridge": {
      "type": "local",
      "command": ["edt-bridge"],
      "environment": {
        "EDTB_EDT_MCP_URL": "http://localhost:8765/mcp"
      }
    }
  }
}
```

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `EDTB_PROJECT_PATH` | Корень EDT-проекта (если не передан `projectPath` в вызове) |
| `EDTB_EDT_MCP_URL` | URL EDT-MCP (по умолчанию `http://localhost:8765/mcp`) |
| `EDTB_ALLOW_FILE_MUTATIONS` | `1` — разрешить файловые правки проекта (по умолчанию запрещены) |

## Безопасность

- Файловые мутации только при `EDTB_ALLOW_FILE_MUTATIONS=1`.
- Перед правкой — backup в `.edtb-backup/<timestamp>/`, ошибка середины пакета откатывает изменения.
- Удаления двухфазные: сначала план с `confirmToken`, затем применение по токену.
- При недоступности EDT-MCP сервер не падает: операции деградируют с warning «выполните resync вручную».
- Warning при незакоммиченных изменениях в git-дереве проекта.

## Разработка

```bash
pip install -e ".[dev]"
python -m pytest tests -q   # 133 теста
```

Спецификация: `SPEC.md`. Гэпы Unica→EDT, которые закрывает проект: `../gaps.md` (в репозитории см. SPEC).

## Ограничения

- Схема `EventHandlerExtension` и условного оформления восстановлена по докам/коду EDT — проверяйте на живом EDT перед продом.
- Точные свойства форм-элементов в `create_metadata` стоит сверять через `get_metadata_details(assignable)`.
- MXL-парсер покрывает основную модель табличного документа; экзотика (вложенные объекты рисунков) — в TODO.
- TODO (требуют живого EDT-MCP для верификации): MCP-хендшейк `initialize`/`notifications.initialized` перед `tools/call`; семантика `resync_to_disk` — проверить, что вызов после файловых правок не затирает их.
- Rollback при ошибке середины пакета гарантирован для `edtb_apply_mutations`; в одиночных операциях bsp/extensions/interface при частичном применении смотрите warnings и backup в `.edtb-backup/`.

## Лицензия

LGPL-3.0 (по аналогии с Unica).
