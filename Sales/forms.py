from django.forms import ModelForm
from django import forms

from Sales.models import Sale, SaleItem

# Create your forms here.

class SaleForm(ModelForm):
    class Meta:
        model = Sale
        fields = ['total_revenue']
        
class SaleFilterForm(forms.Form):
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    select_month = forms.CharField(required=False)
    # search = forms.CharField(required=False)

class SalesReturnFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={
            'placeholder': 'Reference or refund amount',
            'class': 'form-control form-control--polish',
        }),
    )
    reason = forms.ChoiceField(
        required=False,
        choices=[('', 'All reasons')],  # populated in __init__
        widget=forms.Select(attrs={'class': 'form-control form-control--polish'}),
    )
    select_month = forms.CharField(  # browser <input type="month"> gives "YYYY-MM"
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'month',
            'class': 'form-control form-control--polish',
        }),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control--polish',
        }),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control--polish',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from Sales.models import SalesReturn
        self.fields['reason'].choices = [('', 'All reasons')] + list(SalesReturn.REASON_CHOICES)

    def clean_q(self):
        # Strip + return None if empty so the view skips filtering cleanly
        q = (self.cleaned_data.get('q') or '').strip()
        return q or None

    def clean(self):
        cleaned = super().clean()
        sd, ed = cleaned.get('start_date'), cleaned.get('end_date')
        if sd and ed and sd > ed:
            raise forms.ValidationError("From date must be before To date.")
        return cleaned
