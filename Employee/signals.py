from django.db.models.signals import post_delete, post_save


def sync_shift_payroll(sender, instance, **kwargs):
    """Keep Shift.amount equal to the sum of its ShiftEmployee rows.

    A signal rather than a call in the two views, because Shift.amount is a CACHE and a
    cache that any path can forget is the bug this replaced: every Shift in the DB read
    ₱0 while the real payroll was ₱2,000, because clock-in created the row with
    amount=0 and nothing ever went back. Hanging it here means time-in, clock-out,
    owner-close, handover, the admin and a shell script are all covered by construction,
    and a future path gets it for free.
    """
    shift = getattr(instance, 'shift', None)
    if shift is None:            # SET_NULL'd or a half-built row — nothing to total
        return
    shift.recompute_amount()


def archive_employee_on_user_deactivate(sender, instance, **kwargs):
    """Staff user deactivated (is_active=False) → auto-archive their employee record(s).

    One-directional only: reactivation does NOT auto-restore — that stays an
    explicit owner action via restore_employee.
    """
    if instance.is_active:
        return
    from Employee.models import Employee
    Employee.objects.filter(staff_user=instance).update(status='inactive')


def register():
    from user.models import User
    from Employee.models import ShiftEmployee

    post_save.connect(
        archive_employee_on_user_deactivate,
        sender=User,
        dispatch_uid='archive_employee_on_user_deactivate',
    )

    # Both ends: a rate added AND a row removed have to move the total.
    post_save.connect(
        sync_shift_payroll,
        sender=ShiftEmployee,
        dispatch_uid='sync_shift_payroll_on_save',
    )
    post_delete.connect(
        sync_shift_payroll,
        sender=ShiftEmployee,
        dispatch_uid='sync_shift_payroll_on_delete',
    )
