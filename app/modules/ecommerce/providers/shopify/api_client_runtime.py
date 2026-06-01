import time
from urllib.parse import urlparse

import requests
from requests.utils import parse_header_links

from app.config import settings
from app.models.ecommerce import EcommerceConnection
from app.modules.ecommerce.shared.token_service import (
    decrypt_token as _decrypt_token,
)

REQUEST_TIMEOUT = 30
SHOPIFY_API_VERSION = "2025-04"

from app.modules.ecommerce.providers.shopify.http_client import *
from app.modules.ecommerce.providers.shopify.order_api import *
from app.modules.ecommerce.providers.shopify.product_api import *






















