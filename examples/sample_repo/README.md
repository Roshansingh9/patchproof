# sample_repo (synthetic)

A tiny, synthetic shop package used by PatchProof's tests and hosted demo.
It contains a deliberate upstream bug: `shop.coupons.parse_coupon` returns
`None` for unknown coupon codes, and the `None` later reaches arithmetic in
`shop.pricing.apply_discount`. All keys in `shop/settings.py` are fake.
