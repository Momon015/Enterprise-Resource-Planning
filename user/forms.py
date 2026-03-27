from django.forms import ModelForm
from django.contrib.auth.forms import UserCreationForm
from django import forms

from django.contrib.auth.forms import PasswordChangeForm

from user.models import password_validators, User, ROLE_CHOICES



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

    class Meta:
        model = User
        fields = ['owner_username', 'username', 'email', 'password1', 'password2']
        
        
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
    
class UpdateUserForm(ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'birthday', 'phone_number']
        
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
    
