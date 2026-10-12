const createOrder = gql`mutation CreateOrder($customerId: ID!) {
    createOrder(customerId: $customerId) { id }
}`;
const changed = gql`subscription OrderChanged { orderChanged { id } }`;
const profile = gql`query StudentProfileSummary { studentProfileSummary { id } }`;
