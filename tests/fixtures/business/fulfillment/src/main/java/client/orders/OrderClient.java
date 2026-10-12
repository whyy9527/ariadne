public class OrderClient {
    public Order getOrder() { return restClient.get().uri("/orders/{id}").retrieve(); }
}
