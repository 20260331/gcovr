#include "critical.hpp"

// Ordinary, fully covered code.
int helper(int value) {
    return value + 1;
}

int main() {
    helper(1);
    secret_lookup(0);
    return 0;
}
