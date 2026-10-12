@RestController
@RequestMapping("/orders")
public class OrderController {
    @PostMapping("")
    public Order createOrder() { return null; }
    @GetMapping("/{id}")
    public Order getOrder() { return null; }
}
