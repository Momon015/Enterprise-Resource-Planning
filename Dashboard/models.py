from django.db import models
from django.conf import settings
# Create your models here.



class DashboardSeen(models.Model):
    """When a user last opened a business dashboard — powers the
    'While you were away' banner window."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='dashboard_seens')
    business = models.ForeignKey('user.BusinessProfile', on_delete=models.CASCADE,
                                 related_name='dashboard_seens')
    seen_at = models.DateTimeField()

    class Meta:
        unique_together = ('user', 'business')

    def __str__(self):
        return f"{self.user} last saw {self.business} at {self.seen_at}"
