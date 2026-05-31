from django.forms import ModelForm
from django.contrib.auth.forms import UserCreationForm
from django import forms

from django.contrib.auth.forms import PasswordChangeForm

from user.models import password_validators, User, BusinessProfile, ROLE_CHOICES



from django.core.exceptions import ValidationError 

from datetime import date
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
    owner_username = forms.CharField(
        max_length=150,
        required=False,
        help_text="Enter your owner's username if you are registering as staff."
    )
    
    owner_business = forms.CharField(
        max_length=150,
        required=False,
        help_text="Enter your owner's business name if you are registering as staff."
    )

    class Meta:
        model = User
        fields = ['owner_username', 'owner_business', 'username', 'email', 'password1', 'password2']
        
        
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
        fields = ['business_name', 'business_type', 'address', 'business_phone_number']
        
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        disabled_types = ['cafe', 'restaurant']
        choices = self.fields['business_type'].choices
        
        # Keep them but add "Coming Soon" label
        self.fields['business_type'].choices = [
            (k, f"{v} (Coming Soon)") if k in disabled_types else (k, v)
            for k, v in choices
        ]

        # Lock on edit — prevents folder/path drift for uploads
        # and avoids confusing vertical-aware feature gates.
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