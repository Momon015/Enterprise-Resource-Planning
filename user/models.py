from django.db import models

from django.utils.text import slugify
from django.contrib.auth.models import AbstractUser, UserManager

from datetime import date, timedelta

from django.utils import timezone

from django.core.validators import RegexValidator
import random

from decimal import Decimal
import secrets

# Create your models here.

def generate_invite_code():
    return ''.join(secrets.choice('0123456789') for _ in range(10))

class DeleteUnverifiedUserManager(models.Manager):
    def unverified_users(self, minutes=60):
        cutoff = timezone.now() - timedelta(minutes=minutes)
        return self.filter(is_active=False, date_joined__lt=cutoff)

phone_validators = RegexValidator(
    regex=r'^0\d{10}$',
    message="Phone Number must be 11 digits."
)

password_validators = RegexValidator(
    regex=r'^(?=.*[a-z])(?=.*[A-Z])(?=.*[*!#$%^&*])[a-zA-Z0-9!@#$%^&*]{8,}$',
    message='One Lowercase, One Uppercase and One Special symbol minimum'
)

ROLE_CHOICES = [
        ('developer', 'Developer'),
        ('owner', 'Owner'),
        ('staff', 'Staff'),
        
    ]

class User(AbstractUser):
    """
    I override accidentally the UserManager() so I need to manually assign to objects
    """
    objects = UserManager()
    # custom Model Manager for deleting unverified users
    cleanup = DeleteUnverifiedUserManager()
    
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, db_index=True, null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    role = models.CharField(max_length=100, choices=ROLE_CHOICES, null=True, blank=True, default='owner')
    owner = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='staff_members')   
    birthday = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=11, null=True, blank=True, validators=[phone_validators])
    locked_until = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    
    
    
    def __str__(self):
        return f"{self.username}"
    
    def is_locked(self):
        if self.locked_until:
            return timezone.now() < self.locked_until
        return False

  
    def register_failed_login(self):
        if self.is_locked():
            return
        else:
            self.failed_attempts += 1
            if self.failed_attempts > 4 and self.failed_attempts <= 5:
                self.locked_until = timezone.now() + timedelta(minutes=10)
                
    def reset_attempts(self):
        self.failed_attempts = 0
        self.locked_until = None
            

    @property
    def age(self):
        if not self.birthday:
            return None
        
        today = date.today()
        age = today.year - self.birthday.year
        if (today.month, today.day) < (self.birthday.month, self.birthday.day):
            age -= 1
        return age
    
    @property
    def full_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return f"—"
    
    def save(self, *args, **kwargs):
        self.slug = slugify(self.username)
        
        if not self.name:
            self.name = self.first_name
        
        super().save(*args, **kwargs)
        
class EmailOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_otps', null=True, blank=True)
    otp = models.CharField(max_length=6)
    is_verified = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"To: {self.user} - OTP Code: {self.otp} "
    
    def is_expired(self):
        expires_at = self.created_at + timedelta(minutes=5)
        if self.otp:
            return timezone.now() > expires_at
        return False
    
    @classmethod
    def generate_otp(cls):
        return str(random.randint(0, 999999)).zfill(6)

class BusinessProfile(models.Model):
    BUSINESS_TYPE_CHOICE = (
        ('retail', 'Retail'),
        ('cafe', 'Coffee Shop'),
        ('restaurant', 'Restaurant'),
        ('pharmacy', 'Pharmacy'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='business_profiles')
    business_name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255, db_index=True, null=True, blank=True)
    business_type = models.CharField(max_length=255, choices=BUSINESS_TYPE_CHOICE, default='retail')
    offers_services = models.BooleanField(
        default=False,
        help_text="Show the Service Fees catalog (xerox, GCash, bills payment, etc.).",
    )

    business_phone_number = models.CharField(max_length=11, validators=[phone_validators], null=True, blank=True)
    address = models.TextField(null=True, blank=True, max_length=255)
    default_opening_cash = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=Decimal('500.00'),
        help_text="This amount is auto-used as cash float at every clock-in. "
                  "Only set a one-day override below if a specific day needs a different amount."
    )

    # Cash reconciliation feature toggles
    enable_cash_reconciliation = models.BooleanField(
        default=True,
        help_text="When off, staff just clock in/out (no money handling form). "
                  "Turn off if owner is always the cashier."
    )
    
    shared_cash_drawer = models.BooleanField(
        default=True,
        help_text="On: one drawer handed over between cashiers across shifts (AM→PM), "
                  "with a recount at each handover. Off: each cashier has their own till."
    )

    track_coins_separately = models.BooleanField(
        default=False,
        help_text="Split cash float into bills + coins (for shops that leave coins overnight)."
    )
    default_opening_bills = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=Decimal('500.00'),
        help_text="Pre-filled bills portion of cash float (only used when track_coins_separately is on)."
    )
    default_opening_coins = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=Decimal('0.00'),
        help_text="Pre-filled coins portion of cash float (only used when track_coins_separately is on)."
    )
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    invite_code = models.CharField(
        max_length=10, unique=True, db_index=True,
        null=True, blank=True, editable=False,
        help_text="Staff use this code to join this business.",
    )
    accepting_staff = models.BooleanField(
        default=False,
        help_text="When on, staff can register using your invite code. Turn off when you're done hiring.",
    )

    class Meta:
        unique_together = ('user', 'slug')
    
    def save(self, *args, **kwargs):
        if not self.invite_code:
            code = generate_invite_code()
            while BusinessProfile.objects.filter(invite_code=code).exists():
                code = generate_invite_code()
            self.invite_code = code

        base_slug = slugify(self.business_name)
        slug = base_slug
        counter = 1
            
        while BusinessProfile.objects.filter(user=self.user, slug=slug).exclude(id=self.id).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
            
        self.slug = slug 
        
        super().save(*args, **kwargs)
    
    
    def __str__(self):
        return f"{self.business_name} - {self.business_type}"

    
    def regenerate_invite_code(self):
        code = generate_invite_code()
        while BusinessProfile.objects.filter(invite_code=code).exists():
            code = generate_invite_code()
        self.invite_code = code
        self.save(update_fields=['invite_code'])

    @property
    def is_retail(self):
        return self.business_type == 'retail'
    
    @property
    def is_cafe(self):
        return self.business_type == 'cafe'
    
    @property
    def is_restaurant(self):
        return self.business_type == 'restaurant'
    
    @property
    def is_pharmacy(self):
        return self.business_type == 'pharmacy'

    

