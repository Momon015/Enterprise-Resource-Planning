from django.forms import ModelForm
from django import forms

from core.models import Category

# Create your forms here.

class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'category_type', 'target_margin']
        
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Chips, Drinks...',
                'autocomplete': 'off',
                'class': 'form-control',
            }),
            
            'target_margin': forms.NumberInput(attrs={
                'min': '10', 'max': '90', 'inputmode': 'numeric', 'placeholder': 'e.g. 30',
            }),
        }
        
        
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        

        
        
        self.fields['category_type'].empty_label = None
        
        # Material categories are a cafe/restaurant
        if business and business.business_type not in ('cafe', 'restaurant'):
            self.fields['category_type'].choices = [
                c for c in self.fields['category_type'].choices if c[0] != 'material'
            ]
        
        if getattr(user, 'role', None) != 'owner':
            self.fields.pop('target_margin', None)
        else:
            self.fields['target_margin'].required = False
            self.fields['target_margin'].label = 'Target margin %'

        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
            
    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if name.lower() == 'no category':
            raise forms.ValidationError('"No Category" is a reserved name. Please choose a different one.')
        return name


class CategoryFilterForm(forms.Form):
    search = forms.CharField(required=False)