import tabulate


def mktable(data, headers):
    table = tabulate.tabulate(
        data,
        headers=headers,
        tablefmt='fancy_grid',
    )
    return table
