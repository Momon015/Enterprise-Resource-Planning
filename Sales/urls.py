from . import views
from . import api
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    # JSON cart API (React sale-cart island)
    path('api/cart/', api.cart_state, name='cart-state'),
    path('api/cart/qty/', api.cart_set_qty, name='cart-set-qty'),
    path('api/cart/price/', api.cart_set_price, name='cart-set-price'),
    path('api/cart/remove/', api.cart_remove, name='cart-remove'),
    path('api/cart/clear/', api.cart_clear, name='cart-clear'),

    # JSON search API (React sale-search island)
    path('api/sale/search/', api.sale_search, name='sale-search'),
    path('api/sale/add/', api.sale_add, name='sale-add'),

    
    path('clear/session/', views.clear_sale, name='clear-sale'),
    # BIR End-of-Day (Z) reading — owner-only, computed. List (landing) → modal → doc.
    path('z-reading/', views.z_reading_list, name='z-reading-list'),
    path('z-reading/modal/', views.z_reading_modal, name='z-reading-modal'),
    path('z-reading/view/', views.z_reading, name='z-reading'),          # printable doc (iframe target)
    path('z-reading/seal/', views.z_reading_seal, name='z-reading-seal'),  # POST — burn the Z counter

    # sessions
    path('view/list/', views.sale_list, name='sale-list'),
    path('view/list/<int:sale_id>/detail/', views.sale_detail, name='sale-detail'),
    path('add-to-sale/<int:product_id>/', views.add_to_sales, name='add-to-sale'),
    path('view-sale/', views.view_sale, name='view-sale'),
    
    # path('add-employee/', views.add_daily_rate_to_sale, name='add-salary-to-sale'),
    
    path('view/session/sale-summary/', views.view_session_summary, name='view-session-summary'),
    path('view/confirm-summary/', views.confirm_view_summary, name='sale-confirm-summary'),
    path('view/sale/<int:sale_id>/summary/', views.view_sale_summary, name='sale-summary'),
    path('view/sale/<int:sale_id>/void/', views.void_sale, name='void-sale'),

    # reciept
    path('view/sale/<int:sale_id>/receipt/', views.sale_receipt, name='sale-receipt'),
    path('view/sale/<int:sale_id>/receipt/modal/', views.sale_receipt_modal, name='sale-receipt-modal'),

    path('edit/unsold-quantity/<int:product_id>/', views.edit_unsold_quantity, name='sale-edit-unsold-quantity'),
    path('edit/cost-price/<int:product_id>/', views.edit_total_selling_price, name='sale-edit-selling-price'),
    path('edit/prepared-quantity/<int:product_id>/', views.edit_view_sale_quantity, name='sale-edit-quantity'),
    path('delete/prepared-quantity/<int:product_id>/', views.delete_view_sale_quantity, name='sale-delete-quantity'),

    
    # Add sales return
    path('sale/<int:sale_id>/return/create/', views.sales_return_create, name='sales-return-create'),
    path('returns/list/', views.sales_return_list, name='sales-return-list'),
    path('return/<int:return_id>/', views.sales_return_detail, name='sales-return-detail'),
    path('return/<int:return_id>/success/',views.return_recorded,name='sales-return-success'),

    # Add sales payment
    path('receivables/', views.sales_receivables, name='sales-receivables'),
    path('sale/<int:sale_id>/payment/add/', views.add_sales_payment, name='add-sales-payment'),
    path('sale/<int:sale_id>/payment/<int:payment_id>/success/', views.payment_recorded, name='sale-payment-success'),

    # DRAFT sale
    path('drafts/', views.sale_draft_list, name='sale-draft-list'),
    # Review modal — the mandatory look inside a parked sale before Confirm/Cancel act on it.
    path('draft/<int:sale_id>/review/', views.sale_draft_review, name='sale-draft-review'),
    path('draft/<int:sale_id>/confirm/', views.confirm_sale_draft, name='sale-draft-confirm'),
    path('draft/<int:sale_id>/cancel/', views.cancel_sale_draft, name='sale-draft-cancel'),

]