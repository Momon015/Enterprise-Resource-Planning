from django.forms import ModelForm
from django import forms

from core.models import Category

# Create your forms here.

class CategoryForm(ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'category_type']
        
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'e.g. Chips, Drinks...',
                'autocomplete': 'off',
                'class': 'form-control',
            }),
        }
        
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['category_type'].empty_label = None
        # self.fields['category_type'].choices = [('item', 'Item'), ('expense', 'Expense'), ('product', 'Product')]

        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if name.lower() == 'no category':
            raise forms.ValidationError('"No Category" is a reserved name. Please choose a different one.')
        return name


class CategoryFilterForm(forms.Form):
    search = forms.CharField(required=False)