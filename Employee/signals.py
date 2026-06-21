from django.db.models.signals import post_save


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
    post_save.connect(
        archive_employee_on_user_deactivate,
        sender=User,
        dispatch_uid='archive_employee_on_user_deactivate',
    )
