# Example: Spring Kafka Order to Invoice Chain

This example scans the order and invoice services under
[thecodemonkey/kafka-microservices](https://github.com/thecodemonkey/kafka-microservices).
It exercises Kotlin Spring HTTP endpoints plus Kafka producer/consumer links
without starting Kafka or the applications.

The upstream repository is MIT licensed and pinned to
`a4752cdb21d1d06bffb398fd5d128d14a87c6cec`.

## Run it

```bash
git clone https://github.com/whyy9527/ariadne.git
cd ariadne
python -m pip install -e .
python examples/run.py kafka-microservices
```

The runner queries `orders` and verifies that the same returned cluster contains
the order producer and invoice consumer nodes recorded in `expected.json`.
