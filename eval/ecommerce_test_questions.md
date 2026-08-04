# E-commerce (ECOMMERCE_POC) test question bank

A representative bank of realistic business questions against the
`ecommerce-poc` tenant's star schema (`DIM_CUSTOMER`, `DIM_PRODUCT`,
`DIM_DATE`, `DIM_CHANNEL`, `DIM_PROMOTION`, `FACT_ORDERS`,
`FACT_ORDER_ITEMS`). Used to live-test the semantic-completeness fixes in
`LIMITATIONS.md` item 91 (relationship-firing relaxation, the new
`ColumnGlossary`, and the Channel reference-data crawl) across every real
`IntentLabel`, table, and business dimension this dataset supports.

Sections mirror the 4 real `IntentLabel` values plus a few structural
categories (top-N, multi-dimension, named-instance, follow-up) that stress
different parts of the pipeline. A representative subset (not all 100) is
actually run live for this round of verification; the rest seed future
golden-set/regression coverage.

## A. Metric lookup — single-dimension revenue/sales breakdowns (1-15)

1. What is the total revenue by channel?
2. What is the total revenue by product category?
3. What is the total revenue by customer segment?
4. What is the total revenue by loyalty tier?
5. What is the total revenue by country?
6. How many orders were placed by channel?
7. What is the total quantity sold by product category?
8. What is the total discount amount given by channel?
9. What is the average order value by loyalty tier?
10. What is the total shipping cost by channel?
11. How many distinct customers placed an order in each country?
12. What is the total revenue by payment method?
13. What is the total revenue by order status?
14. What is the total tax collected by channel?
15. How many active products are there by category?

## B. Metric lookup — product/promotion/date breakdowns (16-30)

16. What is the total revenue by brand?
17. What is the total revenue by subcategory within Electronics?
18. How many order items used a promotion?
19. What is the total discount amount given by promotion?
20. Which promotion drove the most revenue?
21. What is the total revenue by month?
22. What is the total revenue by quarter?
23. What is the total revenue by year?
24. How does revenue differ between weekend and weekday orders?
25. What is the average unit price by product category?
26. What is the average discount percentage across all promotions?
27. How many products are inactive?
28. What is the total line-item revenue for the Apparel category?
29. What is the total units sold for the Headphones subcategory?
30. What is the total revenue attributable to the Gold loyalty tier?

## C. Comparison (31-45)

31. Compare total revenue between the Website and Mobile App channels.
32. Compare average order value between VIP and New customer segments.
33. Compare total revenue between Electronics and Apparel categories.
34. Compare order counts between Online and Offline channel types.
35. Compare total discount given between the Black Friday and Cyber Monday promotions.
36. Compare revenue between the Bronze and Platinum loyalty tiers.
37. Compare total revenue between the USA and Canada.
38. Compare the number of orders placed on weekends versus weekdays.
39. Compare average unit price between the Books and Electronics categories.
40. Compare total revenue between Q1 and Q4.
41. Compare return rate (Returned order status) between channels.
42. Compare total shipping cost between In-Store and Website orders.
43. Compare total revenue year over year.
44. Compare the number of active versus inactive products.
45. Compare average order value between customers in the USA and the UK.

## D. Trend analysis (46-58)

46. How has total revenue trended month over month?
47. How has the number of orders trended over the last year?
48. How has average order value changed over time?
49. How has revenue from the Mobile App channel trended over time?
50. How has the discount amount given trended by quarter?
51. How has the customer segment mix changed over time?
52. How has revenue in the Electronics category trended month over month?
53. How has the number of new customer signups trended over time?
54. How has the return rate trended over the last two years?
55. How has revenue by loyalty tier trended over time?
56. How has the average discount percentage trended across promotions over time?
57. How has channel mix (share of revenue by channel) shifted over time?
58. How has weekend vs weekday order volume trended?

## E. Anomaly / outlier investigation (59-66)

59. Were there any unusual spikes in revenue by channel?
60. Is there a product category with an unusually high return rate?
61. Are there any months with an unusually low order count?
62. Is there a promotion with an unusually high discount impact compared to others?
63. Are there any customers with an unusually high number of orders?
64. Is there a channel with an unusually high average order value compared to the others?
65. Are there outlier days with unusually high shipping costs?
66. Is there a product with an unusually high quantity sold compared to its category peers?

## F. Top-N / ranking (67-78)

67. What are the top 10 products by revenue?
68. Who are the top 10 customers by total spend?
69. What are the top 5 categories by revenue?
70. What are the top 3 channels by order count?
71. What are the top 5 brands by units sold?
72. What are the top 10 states by revenue?
73. Which promotion generated the most total discount?
74. What are the bottom 5 products by revenue?
75. Which customer segment has the highest average order value?
76. What are the top 5 subcategories by revenue within Apparel?
77. Which loyalty tier contributes the most total revenue?
78. What are the top 10 order dates by total revenue?

## G. Multi-dimension / compound questions (79-90)

79. What is the total revenue by channel and product category?
80. What is the total revenue by loyalty tier and channel?
81. How does average order value vary by customer segment and country?
82. What is the total discount given by promotion and channel?
83. What is the total revenue by category and quarter?
84. How many orders were placed by channel and payment method?
85. What is the return rate by channel and order status?
86. What is the total revenue by country and loyalty tier?
87. What is the average unit price by category and brand?
88. What is the total revenue by month and channel?
89. How does revenue per customer segment differ by channel?
90. What is the total shipping cost by channel and country?

## H. Named-instance / conversational phrasing (91-100)

91. How much revenue came from the Mobile App?
92. How many orders were placed via the Website?
93. What is the total revenue from Electronics?
94. How much did Gold tier customers spend in total?
95. What is the total revenue from customers in the USA?
96. How did the Black Friday Sale perform in terms of revenue?
97. What's our revenue been like this year? (as a follow-up: "What about last year?")
98. Which channel drives the most orders? (as a follow-up: "And which drives the most revenue?")
99. What is the average order value for VIP customers?
100. How many orders came from the Marketplace channel in Q4 2025?

## Coverage notes

- Every real column in the e-commerce `ColumnGlossary` (`add_ecommerce_glossary.py`)
  is exercised by at least one question above.
- All 4 real `IntentLabel` values are covered (sections A/B = `metric_lookup`,
  C = `comparison`, D = `trend_analysis`, E = `anomaly_investigation`).
- Section H specifically stresses the item-86-style named-instance
  matching (Channel reference-data crawl) and the item-91 implied-table
  relationship-firing relaxation, since none of these phrasings say
  "order" literally.
- This bank is a starting point for e-commerce demo coverage, not a
  formal golden set (no `expected_intent`/`expected_tables` YAML per
  question, unlike `eval/golden_set/`) -- promoting a subset into a real
  golden set with expected answers is a reasonable follow-up, not done here.
