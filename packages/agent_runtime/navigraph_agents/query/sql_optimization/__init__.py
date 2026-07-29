"""SQL Optimization agent: applies deterministic, safe rewrites (a hard
row-limit cap, an audit-trail comment, and a WHERE-clause predicate
reorder) to SQL statements produced by the SQL Generation agent, plus
advisory warnings about large unfiltered table scans. Fully deterministic
(pure function of its input) -- see agent.py for the implementation."""
