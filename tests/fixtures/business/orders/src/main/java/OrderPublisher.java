public class OrderPublisher {
    public void publish() { kafkaTemplate.send("order-created", event); }
}
