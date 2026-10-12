from django.contrib import admin

# Disputes are decided in the in-app queue (/disputes/), not the admin, so the
# operator-identity policy applies. Admin has no operator detail (Component 7).
