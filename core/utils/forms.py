"""Form helpers shared by the htmx modal forms."""
from django import forms


def add_duplicate_name_error(form, existing, *, archived, field='name'):
    """Report a name clash ON THE FIELD instead of as a toast + redirect.

    These views used to warn and redirect, which threw away everything the user had
    typed in order to tell them one word was wrong. Worse, the message was queued on
    a path that now re-renders: a modal partial emits no #messages div, so it would
    sit unrendered and pop up on whatever page they opened next.

    The archived case is a DIFFERENT problem to the user — the name looks free from
    where they are standing, so "already exists" reads as a lie. Say where it is.

    `archived` is REQUIRED and keyword-only rather than sniffed off the instance,
    because the models disagree on how they say it: Product uses `is_active`, Material
    uses `status == 'inactive'`. A getattr(…, 'is_active', True) fallback silently
    reads every archived Material as live and tells the user the wrong thing.

    Does NOT preserve a chosen image: no round trip can, because a browser will not
    re-attach a file to a re-rendered input. It keeps the typed fields, which is what
    makes fixing the name cheap instead of starting over.
    """
    if archived:
        form.add_error(field, f"{existing.name} is in your archive. Restore it instead of making a new one.")
    else:
        form.add_error(field, f"{existing.name} already exists. Pick a different name.")


def mark_required(form):
    """Stamp `data-req` on every required field's widget.

    Feeds `templates/partials/_required_guard.html`, which blocks an empty submit
    client-side so the modal never round-trips (and never loses a chosen file) for
    something the page already knows.

    Derived from `field.required` rather than a hand-written list, so it cannot drift
    when a field's required-ness changes — the guard follows the form definition.

    Call at the END of __init__: forms flip `required` in there (MiscExpenseForm makes
    category optional), and this reads the final state.

    Skipped widget types, each for a reason:
      - Hidden/Checkbox: `.value.trim()` is meaningless on them.
      - FileInput: an unfilled file input is exactly the case that CANNOT be re-checked
        after a round trip, but "empty" is legitimate on edit (keep the existing image),
        so requiring one here would block saving a form the server accepts.

    Templates that hand-roll an input instead of rendering `{{ form.x }}` don't go
    through a widget, so they carry a literal `data-req` attribute instead.
    """
    skip = (forms.HiddenInput, forms.FileInput, forms.CheckboxInput)
    for field in form.fields.values():
        if field.required and not isinstance(field.widget, skip):
            field.widget.attrs['data-req'] = True
