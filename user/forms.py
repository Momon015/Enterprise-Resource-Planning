from django.forms import ModelForm
from django.contrib.auth.forms import UserCreationForm
from django import forms

from django.contrib.auth.forms import PasswordChangeForm

from user.models import password_validators, User, BusinessProfile, ROLE_CHOICES

from django.core.exceptions import ValidationError 

from datetime import date

from decimal import Decimal

# Create your forms here.

class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

class BaseUserForm(ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, required=True)
    
class RegisterForm(UserCreationForm):
    
    # Honeypot - must be empty for real users
    # website = forms.CharField(
    #     required=False,
    #     label='',
    #     widget=forms.TextInput(attrs={
    #         'tabindex': '-1',
    #         'autocomplete': 'off',
    #         'aria-hidden': 'true',
    #         'style': 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;',
    #     }),
    # )
    
    # password1 = forms.CharField(
    #     label='Password',
    #     widget=forms.PasswordInput,
    #     validators=[password_validators],
    # )
    
    # password2 = forms.CharField(
    #     label='Confirm Password',
    #     widget=forms.PasswordInput,
    # )
    # owner_username = forms.CharField(
    #     max_length=150,
    #     required=False,
    #     help_text="Enter your owner's username if you are registering as staff."
    # )
    
    # owner_business = forms.CharField(
    #     max_length=150,
    #     required=False,
    #     help_text="Enter your owner's business name if you are registering as staff."
    # )
    
    invite_code = forms.CharField(
        max_length=10, required=False,
        help_text="Enter your owner's invite code if you are registering as staff.",
    )


    class Meta:
        model = User
        fields = ['invite_code', 'username', 'email', 'password1', 'password2']
        
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['email'].help_text = None
        self.fields['email'].label = 'E-mail'
        self.fields['email'].widget.attrs['placeholder'] = 'Enter email'
        
        self.fields['username'].help_text = None
        self.fields['username'].widget.attrs['placeholder'] = 'Enter username'
        
        self.fields['password1'].help_text = None
        self.fields['password1'].widget.attrs['placeholder'] = 'Enter your password'
        self.fields['password2'].widget.attrs['placeholder'] = 'Enter password confirmation'
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
    def clean_email(self):
        email = self.cleaned_data.get('email')
        
        if email and email != self.instance.email:
            qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"This email is already taken.")
        return email
    
    # def clean_website(self):
    #     val = self.cleaned_data.get('website', '')
    #     if val:
    #         # bot caught - silently reject. Don't reveal anything.
    #         raise forms.ValidationError('')
    #     return val
        
    
class UpdateUserForm(ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'birthday', 'phone_number']
        
        widgets = {
            'birthday': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            )
        }
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # self.fields['role'].empty_label = None
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_birthday(self):
        birthday = self.cleaned_data.get('birthday')
        
        if birthday and birthday >= date.today():
            raise ValidationError(f"Birthday must be in the past")
        return birthday
    
class BusinessProfileForm(ModelForm):

    class Meta:
        model = BusinessProfile
        fields = ['business_name', 'business_type', 'address',
                  'street', 'barangay', 'city', 'province', 'region', 'zip_code',
                  'business_phone_number', 'is_vat_registered', 'tin']

        widgets = {
            'is_vat_registered': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        disabled_types = ['cafe', 'restaurant']
        choices = self.fields['business_type'].choices
        self.fields['business_type'].choices = [
            (k, f"{v} (Coming Soon)") if k in disabled_types else (k, v)
            for k, v in choices
        ]

        # Lock on edit — prevents folder/path drift + confusing vertical-aware gates.
        if self.instance and self.instance.pk:
            self.fields['business_type'].disabled = True
            self.fields['business_type'].help_text = (
                "Business type can't be changed after creation. "
                "Contact support if you need to switch — or create a new business."
            )

    def clean(self):
        cleaned_data = super().clean()
        business_type = cleaned_data.get('business_type')
        if business_type in ['cafe', 'restaurant']:
            raise forms.ValidationError(f"{business_type} is coming soon.")
        return cleaned_data

class BusinessFeaturesForm(ModelForm):
    class Meta:
        model = BusinessProfile
        fields = ['offers_services', 'enable_sale_discount', 'enable_purchase_discount',
                  'receipt_width', 'dashboard_basis']
        widgets = {
            'offers_services':          forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'enable_sale_discount':     forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'enable_purchase_discount': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'receipt_width':            forms.Select(attrs={'class': 'form-select'}),
            'dashboard_basis':          forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['offers_services'].label = 'Enable Service Fees'
        self.fields['offers_services'].required = False

        self.fields['enable_sale_discount'].label = 'Customer discounts on sales'
        self.fields['enable_sale_discount'].required = False

        self.fields['enable_purchase_discount'].label = 'Whole-order discount on purchases'
        self.fields['enable_purchase_discount'].required = False

        self.fields['receipt_width'].label = 'Receipt paper width'

        # dashboard_basis only applies to dashboard-tier businesses; drop it otherwise so save can't wipe it.
        self.fields['dashboard_basis'].label = 'Default dashboard lens'
        self.fields['dashboard_basis'].required = False
        try:
            keep_basis = bool(self.instance.pk) and self.instance.plan.has_dashboard()
        except Exception:
            keep_basis = False
        if not keep_basis:
            self.fields.pop('dashboard_basis', None)

    
class BusinessCashDrawerForm(ModelForm):
    class Meta:
        model = BusinessProfile
        fields = [
            'default_opening_cash',
            'enable_cash_reconciliation',
            'shared_cash_drawer',
            'track_coins_separately',
            'default_opening_bills',
            'default_opening_coins',
        ]
        widgets = {
            'default_opening_cash':  forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'default_opening_bills': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'default_opening_coins': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'enable_cash_reconciliation': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'track_coins_separately':     forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
            'shared_cash_drawer':         forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }

    def __init__(self, *args, locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['default_opening_cash'].label = 'Default starting cash'
        self.fields['enable_cash_reconciliation'].label = 'Require cash count at time-out'
        self.fields['track_coins_separately'].label = 'Track bills and coins separately'
        self.fields['default_opening_bills'].label = 'Default bills'
        self.fields['default_opening_coins'].label = 'Default coins'
        self.fields['shared_cash_drawer'].label = 'Cashiers share one drawer'
        for f in ('enable_cash_reconciliation', 'shared_cash_drawer', 'track_coins_separately',
                  'default_opening_bills', 'default_opening_coins'):
            self.fields[f].required = False


        # Lock opening-cash fields while a staff member is mid-shift
        if locked:
            for f in ('default_opening_cash', 'default_opening_bills', 'default_opening_coins'):
                self.fields[f].disabled = True


    def clean(self):
        cleaned = super().clean()
        # When split is on, the float is the sum of bills + coins (mirrors clock_in logic)
        if cleaned.get('track_coins_separately'):
            bills = cleaned.get('default_opening_bills') or Decimal('0')
            coins = cleaned.get('default_opening_coins') or Decimal('0')
            cleaned['default_opening_cash'] = bills + coins
        return cleaned
