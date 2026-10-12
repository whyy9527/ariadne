export class OrderApi extends OrderBackend {
    async getOrder() { return this.axiosRequest.get('/orders/{id}'); }
}
