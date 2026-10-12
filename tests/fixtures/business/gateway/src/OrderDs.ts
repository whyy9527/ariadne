export class OrderDs extends RESTDataSource {
    baseURL = settings.orders.host;
    async createOrder() { return this.post('/orders'); }
    async getOrder() { return this.get('/orders/{id}'); }
}
