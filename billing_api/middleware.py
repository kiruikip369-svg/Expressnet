import time
from collections import defaultdict, deque

from django.conf import settings
from django.http import JsonResponse


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("X-XSS-Protection", "1; mode=block")
        if response.get("Content-Type", "").startswith("text/html"):
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
        return response


class SimpleRateLimitMiddleware:
    buckets = defaultdict(deque)

    def __init__(self, get_response):
        self.get_response = get_response

    def rules(self):
        api_base = settings.API_BASE_PATH.strip("/")
        admin_base = settings.ADMIN_API_PATH.strip("/")
        return {
            ("POST", f"/{api_base}/auth/register"): (5, 15 * 60),
            ("POST", f"/{api_base}/{admin_base}/auth/login"): (5, 15 * 60),
            ("POST", f"/{api_base}/vouchers"): (30, 60 * 60),
        }

    def __call__(self, request):
        if not getattr(settings, "RATE_LIMIT_ENABLED", not settings.DEBUG):
            return self.get_response(request)

        api_base = settings.API_BASE_PATH.strip("/")
        rule = self.rules().get((request.method.upper(), request.path.rstrip("/")))
        if request.method.upper() == "POST" and request.path.startswith(f"/{api_base}/public/") and request.path.rstrip("/").endswith("/pay"):
            rule = (10, 10 * 60)
        if request.method.upper() == "POST" and request.path.startswith(f"/{api_base}/daraja/callback/"):
            rule = (20, 60)
        if rule:
            limit, window = rule
            ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
            key = (request.method.upper(), request.path.rstrip("/"), ip)
            try:
                import redis
                client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
                redis_key = "rl:" + ":".join(str(part) for part in key)
                count = client.incr(redis_key)
                if count == 1:
                    client.expire(redis_key, window)
                if count > limit:
                    return JsonResponse({"message": "Too many attempts. Please try again later."}, status=429)
                return self.get_response(request)
            except Exception:
                pass
            now = time.time()
            bucket = self.buckets[key]
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                return JsonResponse({"message": "Too many attempts. Please try again later."}, status=429)
            bucket.append(now)
        return self.get_response(request)
