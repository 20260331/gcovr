#include "critical.hpp"

// Security critical code, one path is not tested.
int secret_lookup(int key) {
    if (key == 42) {
        return 1;
    }
    return 0;
}
