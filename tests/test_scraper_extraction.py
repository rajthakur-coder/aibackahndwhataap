from app.modules.scraper.engine.scraper import _link_candidates, _socials_from_links


def test_socials_from_nested_firecrawl_links():
    socials = _socials_from_links(
        {
            "external": [
                {"url": "https://www.instagram.com/thehomesenses/"},
                {"href": "https://facebook.com/thehomesenses"},
            ],
            "internal": ["/policies/refund-policy"],
        }
    )

    assert socials == [
        {"type": "instagram", "url": "https://www.instagram.com/thehomesenses"},
        {"type": "facebook", "url": "https://facebook.com/thehomesenses"},
    ]


def test_policy_link_candidates_keep_same_site_policy_pages():
    links = [
        "/policies/refund-policy",
        "https://thehomesenses.in/pages/shipping-policy",
        "https://instagram.com/thehomesenses",
    ]

    assert _link_candidates("https://thehomesenses.in", links, ("refund", "shipping")) == [
        "https://thehomesenses.in/policies/refund-policy",
        "https://thehomesenses.in/pages/shipping-policy",
    ]
