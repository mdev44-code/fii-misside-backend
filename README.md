# Association Village — Backend API

API REST FastAPI pour la gestion digitale d'une association villageoise.

## Stack technique

| Composant | Technologie |
|---|---|
| Framework | FastAPI 0.115 + Python 3.12 |
| Base de données | PostgreSQL 16 (SQLAlchemy async) |
| Cache / Sessions | Redis 7 |
| Migrations | Alembic |
| Auth | JWT (access + refresh tokens) |
| SMS | AfricasTalking |
| Scheduler | APScheduler (cron jobs) |
| Tests | pytest + httpx |
| CI/CD | GitHub Actions → Railway |

## Démarrage rapide

### 1. Prérequis
```bash
python 3.12+
docker & docker compose
poetry
```

### 2. Installation
```bash
git clone <repo>
cd association-backend

# Copie et configure les variables d'environnement
cp .env.example .env
# Édite .env avec tes valeurs (DB, JWT secret, AfricasTalking...)

# Installe les dépendances
make install
```

### 3. Lancer l'environnement complet
```bash
# Démarre PostgreSQL + Redis
make docker-up

# Applique les migrations
make migrate

# Crée le premier admin
python -m app.infrastructure.database.seed

# Lance l'API
make dev
```

L'API est accessible sur `http://localhost:8000`
Documentation Swagger : `http://localhost:8000/api/docs`

## Structure du projet

```
app/
├── main.py                    # Point d'entrée FastAPI
├── config.py                  # Settings (source unique de vérité)
├── dependencies.py            # Dépendances globales (auth, DB)
│
├── domains/                   # Domaines métier (1 dossier = 1 responsabilité)
│   ├── auth/                  # Login, refresh, register via invitation
│   ├── members/               # CRUD membres, organigramme, invitations
│   ├── projects/              # Gestion des projets
│   ├── treasury/              # Caisse : dépôts, dépenses, historique
│   ├── contributions/         # Cotisations mensuelles par membre
│   └── notifications/         # SMS + in-app, canal abstrait (OCP)
│
├── infrastructure/
│   ├── database/              # Modèles, session, migrations Alembic
│   ├── cache/                 # Client Redis
│   ├── scheduler/             # Cron jobs (rappels cotisation)
│   └── security/              # JWT, permissions par rôle
│
└── shared/                    # Exceptions, réponses, enums transversaux
```

## Endpoints principaux

### Auth
| Méthode | Route | Description |
|---|---|---|
| POST | `/api/v1/auth/login` | Connexion (téléphone ou email) |
| POST | `/api/v1/auth/refresh` | Renouveler le token |
| POST | `/api/v1/auth/logout` | Déconnexion |
| POST | `/api/v1/auth/register` | Créer compte depuis invitation |
| GET | `/api/v1/auth/me` | Profil connecté |

### Membres
| Méthode | Route | Rôle requis |
|---|---|---|
| GET | `/api/v1/members` | Tous |
| GET | `/api/v1/members/org-chart` | Tous |
| POST | `/api/v1/members/invite` | Admin |
| PATCH | `/api/v1/members/:id/role` | Admin |
| DELETE | `/api/v1/members/:id` | Admin |

### Caisse
| Méthode | Route | Rôle requis |
|---|---|---|
| GET | `/api/v1/treasury/balance` | Tous |
| POST | `/api/v1/treasury/init` | Comptable |
| POST | `/api/v1/treasury/deposit` | Comptable |
| POST | `/api/v1/treasury/expense` | Comptable |
| GET | `/api/v1/treasury/transactions` | Tous |

### Cotisations
| Méthode | Route | Rôle requis |
|---|---|---|
| POST | `/api/v1/contributions/declare` | Membre |
| GET | `/api/v1/contributions/status` | Tous |

### Projets
| Méthode | Route | Rôle requis |
|---|---|---|
| GET | `/api/v1/projects` | Tous |
| POST | `/api/v1/projects` | Gestionnaire |
| PATCH | `/api/v1/projects/:id` | Gestionnaire |
| DELETE | `/api/v1/projects/:id` | Gestionnaire |

## Tests

```bash
make test              # Tous les tests avec couverture
make test-unit         # Tests unitaires uniquement
make test-integration  # Tests d'intégration uniquement
```

## Commandes utiles

```bash
make lint              # Ruff + mypy
make format            # Formatage automatique
make migration MSG="description de la migration"
make migrate           # Applique les migrations
```

## Flux cotisation

```
Membre → "Déclarer cotisation" → App affiche numéro Wave + montant
→ Membre envoie Wave hors app
→ Comptable reçoit et clique "Confirmer" dans app
→ Caisse incrémentée + notification SMS au membre
→ Cotisation marquée "confirmée"
```

## Variables d'environnement importantes

| Variable | Description |
|---|---|
| `DATABASE_URL` | URL PostgreSQL async |
| `JWT_SECRET_KEY` | Clé secrète JWT (32+ chars) |
| `AFRICASTALKING_API_KEY` | Clé API AfricasTalking |
| `WAVE_TREASURER_NUMBER` | Numéro Wave du comptable affiché aux membres |
| `CONTRIBUTION_AMOUNT` | Montant fixe de cotisation (FCFA) |
| `FIRST_ADMIN_PHONE` | Téléphone du premier admin |
