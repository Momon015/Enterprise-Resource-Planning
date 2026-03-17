from django.db import models
from django.utils.text import slugify

from core.models import TimeStampModel, SlugModel, Category
from Supplier.models import Material
from user.models import User

# Create your models here.

class Stock(TimeStampModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stocks')
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='stocks')
    price = models.DecimalField(decimal_places=6, max_digits=10)
    quantity = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.material.name
    
    @property
    def total_value(self):
        return self.price * self.quantity
    

    