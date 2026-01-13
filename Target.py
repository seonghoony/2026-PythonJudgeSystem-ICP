items = {           
    'A': (3, 60),
    'B': (5, 80),
    'C': (7, 120)
}
W = 12



def all_subsets(items):
    subsets = [[]]
    for key in items:
        n = len(subsets)
        for i in range(n):
            subsets.append(subsets[i] + [key])
    return subsets




def knapsack(items, W):
    best_items = []
    max_value = float('-inf')

    for subset in all_subsets(list(items.keys())):
        total_weight = sum(items[key][0] for key in subset)
        total_value = sum(items[key][1] for key in subset)

        if total_weight <= W and total_value > max_value:
            max_value = total_value
            best_items = subset

    return best_items, max_value


result_items, result_value = knapsack(items, W)
print("아이템 : ", result_items)
print("최대 가치 : ", result_value)
