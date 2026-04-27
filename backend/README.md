# Orbit Backend

MVP backend for user, LLM configuration, conversation, and message storage.

## Local environment

Use the repository-root virtual environment:

```powershell
..\Orbit\Scripts\Activate.ps1
```

Useful checks from `backend/`:

```powershell
..\Orbit\Scripts\python310.exe -B -c "from app.main import app; print(app.title)"
..\Orbit\Scripts\alembic.exe upgrade head --sql
```

