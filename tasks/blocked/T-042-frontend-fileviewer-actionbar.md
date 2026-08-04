---
id: T-042
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: blocked
folders: ["frontend/"]
outputs: [FileViewer component, ActionBar component]
size: S
branch: ~
pr: ~
---

## FileViewer + ActionBar components (frontend/)

**Scope:** `frontend/src/components/FileViewer/` + `frontend/src/components/ActionBar/`.

**Delivers:**
- `FileViewer`: markdown renderer for `eda_report.md` / `final_report.md` and a JSON viewer for `feature_importance` / results
- `ActionBar`: "Submit to Kaggle" button (`POST /submit`) and "Open MLflow" button (`GET /api/mlflow/url` → new tab)

**Done when:**
- [ ] FileViewer renders a markdown fixture as formatted HTML
- [ ] FileViewer renders a JSON fixture in a readable tree/table
- [ ] the Submit button calls `/submit` and shows the returned public score (mocked)
- [ ] the MLflow button opens the URL from `/api/mlflow/url` in a new tab
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` note updated
