# frontend/ — React islands for the ERP

This folder holds **only** the few screens we render with React. Django still
serves all ~107 pages and all auth/sessions/CSRF as before. React just takes over
specific `<div>` slots ("islands") on the pages listed below.

## Islands

| Entry (`src/`)      | Builds to (`../static/react/`) | Mount slot id          |
| ------------------- | ------------------------------ | ---------------------- |
| `sale-cart.jsx`     | `sale-cart.js`                 | `#sale-cart-root`      |
| `purchase-cart.jsx` | `purchase-cart.js`             | `#purchase-cart-root`  |
| `product-list.jsx`  | `product-list.js`              | `#product-list-root`   |

## Mount an island in a Django template

```django
<div id="sale-cart-root" data-business="{{ current_business.slug }}"></div>
<script type="module" src="{% static 'react/sale-cart.js' %}"></script>
```

Pass any initial data via `data-*` attributes, or with `{{ data|json_script:"id" }}`.
A page with no slot renders zero React — nothing changes on the other ~104 pages.

## Commands

Run these from inside `frontend/`:

```bash
npm install      # one time, installs React + Vite
npm run build    # compile islands -> ../static/react/
npm run watch    # rebuild on every save (use while developing)
```

After `npm run build`, run Django normally — it serves the built `.js` from static.

> PATH note: if `npm` isn't found, use the full path on this machine:
> `"C:\Program Files\nodejs\npm.cmd" run build` — or restart VS Code so PATH refreshes.

## Data wiring (for the interactive islands)

The carts need to save changes back to the server. Point `fetch()` at small Django
JSON endpoints (your existing cart views can return `JsonResponse` instead of HTML).
Send the CSRF token from the `csrftoken` cookie on POST. Only these few screens need
endpoints — the rest of the app needs no API.
