"""The Settings tab's panels, one module each: server (with model_card, the MODEL card below it),
models, autofit, tune, router and caching; pickers (the drop-downs and questions), model_lines
(the model lists) and common (pieces several panels draw). settings_view.SettingsView puts them
together. Pure: they draw from the UI state and read models and config.json through
SettingsService (the ModelStore port)."""
