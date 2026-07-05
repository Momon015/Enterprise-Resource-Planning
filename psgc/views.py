from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .models import Region, Province, CityMunicipality, Barangay

# Create your views here. 

@login_required(login_url='login')
def regions(request):
    return JsonResponse(list(Region.objects.values('id', 'name')), safe=False)

@login_required(login_url='login')
def provinces(request):
    rid = request.GET.get('region_id')
    qs = Province.objects.filter(region_id=rid) if rid else Province.objects.none()
    return JsonResponse(list(qs.values('id', 'name')), safe=False)

@login_required(login_url='login')
def cities(request):
    rid = request.GET.get('region_id')
    pid = request.GET.get('province_id')
    if pid:
        qs = CityMunicipality.objects.filter(province_id=pid)
    elif rid:                                  # NCR / province-less
        qs = CityMunicipality.objects.filter(region_id=rid, province__isnull=True)
    else:
        qs = CityMunicipality.objects.none()
    return JsonResponse(list(qs.values('id', 'name')), safe=False)

@login_required(login_url='login')
def barangays(request):
    cid = request.GET.get('city_id')
    qs = Barangay.objects.filter(city_id=cid) if cid else Barangay.objects.none()
    return JsonResponse(list(qs.values('id', 'name')), safe=False)
