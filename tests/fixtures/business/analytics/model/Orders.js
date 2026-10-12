cube('Orders', {
  measures: {
    orderCount: { type: 'count' }
  },
  dimensions: {
    orderId: { type: 'string' }
  }
});
