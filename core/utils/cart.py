"""Session-cart helpers shared by the sale and purchase carts."""


def prune_stale_cart_lines(request, business, session_key, model):
    """Drop cart lines whose item no longer resolves through the model's
    default (active-only) manager — e.g. it was archived or deleted while
    sitting in the session cart. Without this, cart-walking views 404 the
    whole page on the first stale line.

    Returns the (possibly cleaned) cart dict and keeps the session in sync.
    """
    cart = request.session.get(session_key, {}) or {}
    if not cart:
        return cart

    ids = [key for key in cart.keys() if str(key).isdigit()]
    alive = {
        str(pk) for pk in model.objects.filter(
            business=business, id__in=ids
        ).values_list('id', flat=True)
    }
    stale = [key for key in cart if str(key) not in alive]
    if stale:
        for key in stale:
            cart.pop(key, None)
        request.session[session_key] = cart
        request.session.modified = True
    return cart
