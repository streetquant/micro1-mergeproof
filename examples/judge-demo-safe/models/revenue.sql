select
    cast(100 as decimal(12, 2)) as sales,
    cast(20 as decimal(12, 2)) as refunds,
    cast(100 - 20 as decimal(12, 2)) as net_revenue
