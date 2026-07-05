from django.db import models

# Create your models here.

class Region(models.Model):
    code = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Province(models.Model):
    code = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='provinces')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CityMunicipality(models.Model):
    code = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    # province is NULL for NCR / independent Highly-Urbanized Cities…
    province = models.ForeignKey(Province, on_delete=models.CASCADE,
                                 related_name='cities', null=True, blank=True)
    # …so region is ALWAYS set — it's what makes the NCR cascade work.
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='cities')

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Cities / Municipalities'

    def __str__(self):
        return self.name


class Barangay(models.Model):
    code = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    city = models.ForeignKey(CityMunicipality, on_delete=models.CASCADE, related_name='barangays')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    