# paKITA

A web-based sales and inventory system for Filipino small business owners — cafés, retail shops, small restaurants. Manage sales, inventory, purchases, expenses, waste/loss, and employees from one dashboard.

Built with Django. Solo-developer project, pre-launch.

---

## Tech stack

- **Backend:** Django (Python)
- **Database:** SQLite (development), Postgres (production)
- **Frontend:** Django templates, Bootstrap 5.3, Bootstrap Icons, htmx
- **Email:** SMTP via Django's `EmailMultiAlternatives`
- **Auth:** Custom `User` model extending `AbstractUser`, with `role` (owner / staff / developer) and `BusinessProfile` ownership

## Project structure

```
SalesAndInventorySystem/      # Django project settings
core/                         # Shared utilities (StatusModel, Category, owner helpers, email helper)
user/                         # User, BusinessProfile, EmailOTP, auth flows
subscription/                 # Per-business plans, founder system, capacity decorator
Product/                      # Product, ProductPreset
Supplier/                     # Supplier, Material, MaterialPreset
Sales/                        # Sale, SaleItem, SaleEmployee
Expense/                      # Purchase, Employee, Waste, Expense, MiscExpense, Shift
Inventory/                    # Stock (per-business inventory rollup)
DailySummary/                 # Daily/monthly/weekly summary reports
Dashboard/                    # Pro-tier dashboard with KPIs and charts
templates/                    # Base templates (main.html, navbar.html, landing_page.html)
static/                       # CSS, JS, images
```

## Local setup

```bash
# Clone and enter the directory
git clone <repo-url>
cd SalesAndInventorySystem

# Create and activate a virtual environment
python -m venv env
# Windows
env\Scripts\activate
# macOS / Linux
source env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables (see Environment section below)
# Then run migrations
python manage.py migrate

# Create a superuser for /admin/
python manage.py createsuperuser

# Run the dev server
python manage.py runserver
```

Access the app at `http://127.0.0.1:8000/` and the admin at `http://127.0.0.1:8000/admin/`.

## Environment variables

Create a `.env` file (or set in your shell):

```
DJANGO_SECRET_KEY=<long-random-string>
DJANGO_DEBUG=True
EMAIL_HOST_USER=<smtp-sender-email>
EMAIL_HOST_PASSWORD=<smtp-app-password>
SUPPORT_EMAIL=<email-for-contact-form-submissions>
```

## Roles

- **owner** — creates and owns `BusinessProfile`s. Full access to all features per their plan tier.
- **staff** — registered under an owner's business. Limited visibility: can only see sales / purchases / waste records they personally created. Owner-only sections (financial reports, dashboard) are blocked.
- **developer** — internal account with read-only access across all data. Used for debugging / support.

## Architecture highlights

### Per-business plans
Each `BusinessProfile` has its own `BusinessPlan` (Free / Standard / Premium / Pro). An owner can run multiple businesses on different tiers. Billing = sum across all businesses on the account.

### Bundle = slot cap
`Subscription.bundle` (single / dual / triple) is the maximum number of business slots, not a paid product. Defaults to triple (3 slots). The owner pays per-business plan; bundle just caps how many they can create.

### Lockable items
When an owner downgrades, items over the new cap are **locked** (`is_locked=True`), not deleted. They reappear on re-upgrade, or the owner can manually pick which N items stay active. Applies to: Product, Material, Supplier, Employee, ProductPreset, MaterialPreset.

### Monthly reset for transactional caps
Sales / Purchases / Waste / Expenses caps reset on the 1st of each calendar month. Free tier's "10 sales" means "10 per calendar month", not "10 lifetime". Old records remain visible — only new creates are blocked once at-cap.

### Trial system
One 14-day Premium-or-Pro trial per account. Owner selects one business to apply it to. After expiry, that business auto-downgrades to Free. `Subscription.trial_used` prevents future trials.

### Founders
First 10 accounts can claim a founder code → permanently locked discount pricing. Tracked via `FounderInvite` codes and `FounderSlot` singleton.

### Feature gating
`subscription/decorators.py` provides:
- `@capacity_required(key)` — gates a view by the business's plan capacity (e.g. `'product'`, `'sale'`)
- `@feature_required(method_name)` — gates a view by a plan-tier feature flag (e.g. `'has_dashboard'`)

Templates check the same flags via `current_business.plan.has_dashboard`, etc.

### Anti-abuse
- 60-second rate limit on business creation (with soft "I'm human" checkbox if hit)
- Hidden honeypot field on register, login, and contact form
- Email OTP verification on signup
- Hard cap of 3 businesses per account (bundle ceiling)

## Plan tiers (regular pricing)

| Tier     | ₱/mo   | Limits |
|----------|--------|--------|
| Free     | ₱0     | 10 products, 10 materials, 2 suppliers, 0 staff, monthly caps on transactions |
| Standard | ₱300   | 30 products, 30 materials, 5 suppliers, 1 staff, 30/month transactions |
| Premium  | ₱1,299 | Unlimited products/materials, 10 suppliers, 5 staff, unlimited transactions, daily + monthly reports |
| Pro      | ₱1,499 | Unlimited everything, 10 staff, dashboard access, weekly + daily + monthly reports |

Multi-business surcharges (regular pricing): Standard +₱150 per extra, Premium +₱600 per extra, Pro +₱700 per extra.

Yearly discounts: Premium 15%, Pro 17%. Standard no discount.

Founders get permanent locked pricing (Standard ₱300, Premium ₱800, Pro ₱1,000) and +50% surcharge on Premium/Pro extras.

## Development notes

### Templates
- Base: `templates/main.html`. Includes navbar, locked-items banner block, and content block.
- Cache-bust CSS by bumping `?v=N` in `templates/main.html` whenever `static/styles/style.css` changes.

### Mobile / responsive
- Mobile breakpoint: ≤767px
- Utility classes: `.col-mobile-hidden`, `.col-mobile-only`
- Mobile-specific patterns: `.mobile-back-row` (chevron back button), `.mobile-bottom-nav` (fixed bottom nav), `.sidebar-mobile-bar` (hamburger header)

### Migrations
- Always commit migrations alongside model changes
- Data backfills should be separate migrations (use `RunPython`) for clarity
- Never edit or delete an applied migration — write a new one on top

### Dashboard caching
Heavy aggregate queries are cached per-business-per-date with a 5-minute TTL via Django's cache framework. See `Dashboard/views.py:_get_cached_dashboard_metrics`. Default `LocMemCache` works in dev; production should use Redis.

### Email
All outbound email goes through `core/utils/email.py`'s `send_email()` helper (OTP-specific) or `EmailMultiAlternatives` directly (contact form). The `EMAIL_HOST_USER` is the "from" address; `SUPPORT_EMAIL` is the destination for contact form submissions.

## License

TBD — solo developer project, not yet open-sourced.

## Status

Pre-launch. Active development. No public users. Targeting Filipino SMB segment.
