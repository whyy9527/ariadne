@RestController
public class WhiteboardController {
    @PutMapping(
        value = "/practices/{practiceId}/whiteboard",
        consumes = APPLICATION_JSON_VALUE
    )
    @PreAuthorize("isAuthenticated()")
    public void saveWhiteboard() {}

    @GetMapping(path = "/practices/{practiceId}/whiteboard", produces = APPLICATION_JSON_VALUE)
    public String getWhiteboard() { return null; }
}
