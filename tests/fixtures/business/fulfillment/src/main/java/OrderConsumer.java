public class OrderConsumer {
    @KafkaListener(topics = "order-created")
    public void acceptOrder() {}
}
