# Contributing to V.I.O.L.E.T.

V.I.O.L.E.T. (Visual Image Organizer for Local Evaluation & Tagging) extends [Blombooru](https://github.com/mrblomblo/blombooru) with local library scanning, AI auto-tagging, and more. Contributions are welcome!

## Getting Started

### Setting Up for Development

1. **Clone the repository**

    ```powershell
    git clone https://github.com/kyloris0660/AnimeLocalBooru.git
    cd AnimeLocalBooru
    ```

2. **Set up your environment**

    ```powershell
    python -m venv venv
    .\venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    ```

    Create `.env` from the example and edit it (at minimum, set your PostgreSQL password):

    ```powershell
    Copy-Item example.env .env
    ```

    Run the server in debug mode:

    ```powershell
    python run.py --debug
    ```

3. **Complete the onboarding**

    Open `http://localhost:8000` and go through the first-time setup.

### Project Structure

| Directory | Description |
|:----------|:------------|
| `backend/app/` | FastAPI backend — routes, models, services, utilities |
| `backend/app/routes/` | API route handlers |
| `backend/app/services/` | Business logic and service layer |
| `backend/app/utils/` | Shared utilities |
| `frontend/templates/` | Jinja2 HTML templates |
| `frontend/static/` | Static assets (CSS, JS, images) |
| `frontend/static/css/themes/` | Theme CSS files |
| `data/` | Runtime data (settings, etc.) — not committed to Git |
| `media/` | Uploaded media and thumbnails — not committed to Git |
| `docs/` | Project documentation |
| `scripts/` | Developer and maintenance scripts |
| `tests/` | pytest and Playwright E2E tests |

## Workflow

### Branch and PR

- **Branch from `main`** for all changes.
- **One PR per feature or fix.** Keep PRs focused.
- **Do not push directly to `main`.** All changes go through pull requests.
- The maintainer reviews and merges PRs on GitHub.

### Phase Plan Approval

For major features or substantial scope changes (new classifiers, DB schema changes, evaluation frameworks, etc.), produce an implementation plan and wait for maintainer approval before writing code. Bug fixes and small adjustments may proceed directly.

### Browser Validation

UI-affecting changes must include real browser validation before delivery. Prefer Playwright with Edge on Windows. API-only or unit tests are not sufficient when UI behavior is affected. See `CLAUDE.md` for the full validation standard.

## Code Contributions

### General Guidelines

- **Test your changes.** Run `python -m pytest tests/ -x -q` before submitting. If your change touches UI, run relevant Playwright tests as well.
- **Don't break existing functionality.** If modifying existing behavior, explain why in the PR description.
- **Follow the existing code style.** Match surrounding patterns.
- **Do not commit secrets.** Never commit `.env`, API keys, database credentials, or model files. The `.gitignore` handles most of these, but double-check before committing.

### Backend Changes

The backend is a [FastAPI](https://fastapi.tiangolo.com/) application. Routes live in `backend/app/routes/`, models in `backend/app/models.py`, and business logic in `backend/app/services/`. The server auto-reloads in debug mode.

### Frontend Changes

The frontend uses vanilla JavaScript, Jinja2 templates, and [Tailwind CSS](https://tailwindcss.com/). Stylesheets are built using a local Tailwind setup (see the `tailwind/` directory).

> [!NOTE]
> If your changes involve Tailwind classes, you may need to rebuild the CSS.
>
> 1. Download the standalone Tailwind CLI (v4.2.1) from [their releases page](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.2.1).
> 2. Move the downloaded executable into the `tailwind/` directory.
> 3. Run the executable to build the CSS:
>    ```powershell
>    cd tailwind
>    .\tailwindcss-windows-x64.exe -i input.css -o ..\frontend\static\css\tailwind.css --minify
>    ```

### Database Migrations

V.I.O.L.E.T. uses a DIY migration system in `backend/app/database.py`. If your changes require schema modifications, write a migration function that checks whether the change already exists before applying it. Add it to the `migrations` list inside `check_and_migrate_schema`. Never drop columns or tables without migrating existing data first.

## Themes

Theme contributions follow the same process as upstream Blombooru. See the `frontend/static/css/themes/` directory for examples. Register new themes in `backend/app/themes.py`.

## Translations

V.I.O.L.E.T. uses zh-CN as the primary UI language with English fallback. Locale files are in `frontend/static/locales/`. Dynamic tag translations are handled separately via the LLM translation system — see `docs/tag-localization-zh.md` and `docs/tag-localization-llm.md`.

## Reporting Issues

Please use the [GitHub issue tracker](https://github.com/kyloris0660/AnimeLocalBooru/issues).

## License

By contributing to V.I.O.L.E.T., you agree that your contributions will be licensed under the [MIT License](LICENSE.txt).
