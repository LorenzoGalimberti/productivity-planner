# Productivity Planner Agent

An autonomous AI productivity assistant built with Django, Django REST Framework, and LangGraph.

The agent helps users plan their day and week based on tasks, deadlines, priorities, calendar constraints, and personal preferences.

## Architecture

```
productivity_planner/
├── config/              # Django settings, URLs, WSGI
├── apps/
│   ├── users/           # Custom User model & auth
│   ├── planner/         # Task, CalendarEvent, PlanningSession models
│   ├── memory/          # UserPreference, MemoryNote, behavioral tracking
│   ├── tools/           # Mock tool services (calendar, tasks, reminders)
│   ├── agents/          # LangGraph workflow & agent logic
│   └── api/             # DRF views, serializers, URL routing
├── templates/           # HTML templates (optional frontend)
├── static/              # Static assets
├── tests/               # Test suite
├── manage.py
├── requirements.txt
└── .env.example
```

## Setup

```bash
# 1. Clone and enter the project
cd productivity_planner

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# 5. Run migrations
python manage.py migrate

# 6. Create superuser
python manage.py createsuperuser

# 7. Run server
python manage.py runserver
```

## API Endpoints

| Method | Endpoint            | Description              |
|--------|---------------------|--------------------------|
| GET    | `/api/health/`      | Health check             |
| POST   | `/api/plan/day/`    | Generate daily plan      |
| POST   | `/api/plan/week/`   | Generate weekly plan     |
| POST   | `/api/replan/`      | Replan/reschedule        |
| GET    | `/api/tasks/`       | List user tasks          |
| POST   | `/api/tasks/create/`| Create a new task        |

## Tech Stack

- **Backend**: Django 5.1, Django REST Framework
- **AI Agent**: LangGraph (state-based workflow)
- **LLM**: OpenAI GPT-4o-mini (configurable)
- **Database**: SQLite (dev) / PostgreSQL (prod)
- **Validation**: Pydantic v2

## Development Steps

- [x] Step 1: Project structure
- [ ] Step 2: Models
- [ ] Step 3: DRF APIs
- [ ] Step 4: Tool layer
- [ ] Step 5: LangGraph agent workflow
- [ ] Step 6: Memory integration
- [ ] Step 7: Tests
- [ ] Step 8: UI (optional)
