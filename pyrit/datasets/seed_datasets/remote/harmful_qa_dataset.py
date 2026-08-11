# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)

# HarmfulQA's ``topic`` and ``subtopic`` fields are academic subject areas, not harm
# labels, and most subtopics span several distinct harms, so a coarse topic->category
# mapping is misleading. Instead each question was audited individually and its canonical
# harm category recorded below, keyed by the upstream row ``id`` (a stable, globally
# unique integer, 1..1960). Keying by id -- rather than by the question text -- means this
# repository does not redistribute the dataset's harmful prompts.
#
# Authored grouped by category so the audit is reviewable ("are these really OTHER?"); the
# flat id->category index used at load time is derived from it once, below.
# fmt: off
_IDS_BY_HARM_CATEGORY: dict[HarmCategory, tuple[int, ...]] = {
        HarmCategory.HATE_SPEECH: (
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 85, 92, 100, 174, 185, 1252,
            1289, 1299, 1369, 1379, 1444, 1464, 1472, 1477, 1483, 1490, 1494, 1546, 1557, 1762, 1802, 1803, 1808,
            1809, 1813, 1814, 1815, 1904, 1909, 1914, 1915, 1916, 1917, 1918, 1919, 1920
        ),
        HarmCategory.HARASSMENT: (
            145, 160, 1548, 1553, 1601, 1603, 1604, 1605, 1606, 1607, 1608, 1609, 1610, 1611, 1612, 1613, 1614,
            1615, 1616, 1617, 1618, 1619, 1620, 1682, 1683, 1684, 1685, 1686, 1687, 1688, 1689, 1690, 1691, 1692,
            1693, 1694, 1695, 1696, 1697, 1698, 1699, 1700
        ),
        HarmCategory.VIOLENT_CONTENT: (
            1374, 1445, 1458, 1550
        ),
        HarmCategory.SEXUAL_CONTENT: (
            1380, 1446, 1459
        ),
        HarmCategory.REPRESENTATIONAL: (
            141, 142, 143, 144, 146, 147, 148, 149, 151, 152, 153, 154, 155, 156, 158, 159, 253, 254, 257, 370, 375,
            431, 810, 815, 843, 846, 850, 852, 855, 858, 983, 995, 999, 1028, 1187, 1334, 1421, 1422, 1423, 1424,
            1425, 1426, 1427, 1428, 1429, 1430, 1431, 1432, 1433, 1434, 1435, 1436, 1437, 1438, 1439, 1440, 1570,
            1573, 1577, 1624, 1630, 1640, 1651, 1662, 1663, 1664, 1665, 1666, 1667, 1668, 1669, 1670, 1671, 1672,
            1673, 1674, 1675, 1676, 1677, 1678, 1679, 1680, 1724, 1726, 1733, 1738, 1739, 1740, 1744, 1748, 1752,
            1753, 1825, 1830, 1831, 1832, 1836, 1837, 1838
        ),
        HarmCategory.SELF_HARM: (
            43, 48, 49, 51, 52, 53, 821, 1658
        ),
        HarmCategory.DRUG_USE: (
            42, 781, 782, 784, 785, 787, 788, 789, 790, 791, 798, 803, 807, 811, 816, 819, 1377
        ),
        HarmCategory.VIOLENT_THREATS: (
            83, 96, 150, 157, 183, 861, 862, 863, 864, 865, 866, 867, 868, 869, 870, 871, 872, 873, 874, 875, 876,
            877, 878, 879, 880, 1246, 1260, 1285, 1297, 1465, 1473, 1764, 1769, 1804, 1844, 1855, 1908
        ),
        HarmCategory.VIOLENT_EXTREMISM: (1373,),
        HarmCategory.REGULATED_GOODS: (
            517, 783
        ),
        HarmCategory.SCAMS: (
            101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 243,
            248, 250, 251, 331, 349, 363, 366, 374, 377, 380, 391, 441, 442, 443, 444, 445, 446, 447, 448, 449, 450,
            451, 452, 453, 454, 455, 456, 457, 458, 459, 460, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491,
            492, 493, 494, 495, 496, 497, 498, 499, 500, 501, 502, 503, 504, 506, 507, 508, 509, 510, 511, 512, 513,
            514, 515, 516, 519, 520, 521, 522, 523, 524, 525, 526, 527, 528, 529, 530, 531, 532, 533, 534, 535, 536,
            537, 538, 539, 540, 541, 542, 543, 544, 545, 546, 547, 548, 549, 550, 551, 552, 553, 554, 555, 556, 557,
            558, 560, 646, 650, 654, 657, 666, 671, 808, 809, 814, 817, 820, 948, 957, 1163, 1172, 1175, 1326, 1331,
            1333, 1703, 1709, 1713, 1715, 1882, 1883, 1888, 1889, 1891, 1894, 1898, 1921, 1922, 1925, 1930, 1935,
            1940
        ),
        HarmCategory.IMPERSONATION: (
            1711, 1719
        ),
        HarmCategory.INFO_INTEGRITY: (
            84, 184, 945, 949, 1021, 1022, 1023, 1024, 1027, 1029, 1030, 1032, 1033, 1034, 1035, 1036, 1037, 1038,
            1039, 1040, 1083, 1086, 1088, 1100, 1165, 1302, 1308, 1309, 1314, 1466, 1474, 1475, 1485, 1491, 1549,
            1551, 1556, 1559, 1561, 1562, 1563, 1564, 1565, 1566, 1567, 1568, 1569, 1572, 1574, 1576, 1578, 1579,
            1580, 1627, 1741
        ),
        HarmCategory.HISTORICAL_EVENTS_BIAS: (
            1761, 1763, 1765, 1766, 1767, 1768, 1770, 1771, 1772, 1773, 1774, 1775, 1776, 1777, 1778, 1779, 1780,
            1801, 1805, 1806, 1807, 1810, 1811, 1812, 1816, 1817, 1818, 1819, 1820, 1861, 1862, 1863, 1864, 1865,
            1866, 1867, 1868, 1869, 1870, 1871, 1872, 1873, 1874, 1875, 1876, 1877, 1878, 1879, 1880, 1901, 1902,
            1903, 1905, 1906, 1907, 1910, 1911, 1912, 1913, 1923, 1924, 1926, 1927, 1928, 1929, 1931, 1932, 1933,
            1934, 1936, 1937, 1938, 1939
        ),
        HarmCategory.ELECTION_INTEGRITY: (
            61, 64, 67, 69, 247, 365
        ),
        HarmCategory.DECEPTION: (
            81, 82, 86, 87, 88, 89, 90, 91, 93, 94, 95, 97, 98, 99, 161, 162, 164, 165, 166, 167, 168, 169, 170,
            171, 172, 173, 175, 176, 177, 178, 179, 180, 181, 182, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195,
            196, 197, 198, 199, 200, 241, 242, 245, 246, 249, 252, 255, 256, 258, 259, 260, 301, 302, 303, 304, 305,
            306, 307, 308, 309, 311, 313, 315, 317, 319, 348, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360,
            361, 362, 364, 368, 369, 371, 372, 373, 376, 378, 379, 401, 402, 403, 404, 405, 406, 407, 408, 409, 410,
            411, 412, 413, 414, 415, 416, 417, 418, 419, 420, 421, 422, 424, 425, 426, 427, 428, 429, 430, 432, 433,
            434, 435, 436, 437, 438, 439, 440, 461, 462, 463, 464, 465, 466, 467, 468, 469, 470, 471, 472, 473, 474,
            475, 476, 477, 478, 479, 480, 561, 564, 565, 566, 567, 568, 571, 572, 573, 574, 575, 576, 577, 578, 579,
            580, 581, 582, 584, 585, 586, 587, 589, 590, 591, 592, 593, 594, 595, 596, 597, 598, 599, 600, 643, 645,
            649, 652, 656, 659, 802, 804, 805, 806, 813, 818, 842, 844, 847, 848, 851, 853, 856, 859, 961, 962, 964,
            965, 966, 968, 969, 970, 971, 972, 973, 974, 976, 977, 978, 979, 980, 1001, 1002, 1003, 1004, 1005,
            1006, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019, 1020, 1261, 1262,
            1263, 1264, 1265, 1266, 1267, 1268, 1269, 1270, 1271, 1272, 1273, 1274, 1275, 1276, 1277, 1278, 1279,
            1280, 1301, 1303, 1304, 1305, 1306, 1307, 1310, 1311, 1312, 1315, 1316, 1317, 1318, 1319, 1320, 1402,
            1403, 1404, 1405, 1407, 1410, 1412, 1414, 1415, 1417, 1419, 1462, 1463, 1467, 1468, 1469, 1470, 1471,
            1476, 1478, 1479, 1480, 1481, 1482, 1484, 1486, 1487, 1488, 1489, 1492, 1493, 1495, 1496, 1497, 1498,
            1499, 1500, 1581, 1582, 1583, 1584, 1585, 1586, 1587, 1588, 1589, 1590, 1591, 1592, 1593, 1594, 1595,
            1596, 1597, 1598, 1599, 1600, 1661, 1721, 1722, 1723, 1725, 1727, 1728, 1729, 1730, 1731, 1732, 1734,
            1735, 1736, 1737, 1745, 1781, 1785, 1786, 1790, 1821, 1822, 1823, 1824, 1826, 1827, 1828, 1829, 1833,
            1834, 1835, 1839, 1840, 1843, 1845, 1849, 1852, 1856, 1858, 1887, 1893, 1895, 1897, 1899
        ),
        HarmCategory.COVERT_TARGETED: (
            21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 44, 45, 46, 47, 50,
            54, 55, 56, 57, 58, 59, 60, 62, 63, 65, 66, 68, 70, 72, 73, 74, 75, 77, 78, 79, 201, 202, 203, 204, 205,
            206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 269, 270, 271, 272, 273, 274,
            275, 276, 277, 278, 279, 280, 701, 702, 703, 704, 705, 706, 707, 708, 709, 710, 713, 765, 777, 1142,
            1143, 1144, 1145, 1146, 1147, 1148, 1149, 1150, 1151, 1152, 1153, 1154, 1155, 1156, 1157, 1158, 1159,
            1160, 1181, 1182, 1183, 1184, 1185, 1186, 1188, 1189, 1190, 1191, 1192, 1193, 1194, 1195, 1196, 1197,
            1198, 1199, 1200, 1201, 1202, 1203, 1204, 1205, 1206, 1207, 1208, 1209, 1210, 1211, 1212, 1213, 1214,
            1215, 1216, 1217, 1218, 1219, 1220, 1241, 1242, 1243, 1244, 1245, 1247, 1248, 1249, 1250, 1251, 1253,
            1254, 1255, 1256, 1257, 1258, 1259, 1281, 1282, 1283, 1284, 1286, 1287, 1288, 1290, 1291, 1292, 1293,
            1294, 1295, 1296, 1298, 1300, 1313, 1321, 1323, 1324, 1325, 1328, 1329, 1330, 1332, 1335, 1336, 1337,
            1338, 1339, 1340, 1341, 1342, 1343, 1344, 1345, 1346, 1347, 1348, 1349, 1350, 1351, 1353, 1354, 1355,
            1356, 1358, 1359, 1360, 1541, 1544, 1545, 1547, 1552, 1554, 1555, 1558, 1560, 1641, 1642, 1643, 1644,
            1645, 1646, 1647, 1648, 1649, 1650, 1652, 1653, 1654, 1655, 1656, 1657, 1659, 1660
        ),
        HarmCategory.REPUTATIONAL_DAMAGE: (1443,),
        HarmCategory.PLAGIARISM: (
            281, 282, 283, 285, 287, 288, 289, 291, 292, 967, 1322, 1327, 1361, 1365, 1401, 1406, 1442, 1450, 1461,
            1501, 1502, 1503, 1504, 1505, 1506, 1507, 1508, 1509, 1510, 1511, 1512, 1513, 1515, 1516, 1517, 1519,
            1520, 1542, 1571, 1575, 1621, 1681, 1701, 1702, 1705, 1706, 1707, 1708, 1710, 1712, 1714, 1716, 1717,
            1718, 1720, 1751, 1886, 1892
        ),
        HarmCategory.PROPRIETARY_INFO: (
            71, 76, 80, 423, 505, 559, 562, 963, 975, 1025, 1026
        ),
        HarmCategory.PPI: (
            569, 583, 588, 685, 801, 812, 841, 845, 849, 854, 857, 860, 1031, 1622, 1632
        ),
        HarmCategory.INSECURE_CODE: (
            321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 332, 333, 334, 335, 336, 337, 338, 339, 340, 367, 382,
            383, 385, 388, 394, 395, 396, 397, 398, 399, 400, 622, 624, 629, 638, 661, 663, 664, 665, 668, 670, 672,
            674, 675, 678, 679, 680, 682, 693, 1173, 1179, 1626, 1704
        ),
        HarmCategory.MALWARE: (
            386, 387, 389, 390, 392, 393, 662, 667, 669, 673, 676, 677, 692, 1636
        ),
        HarmCategory.MILITARY: (
            345, 694, 700
        ),
        HarmCategory.CBRN: (
            721, 722, 725, 726, 727, 728, 729, 730, 731, 732, 733, 734, 735, 736, 737, 738, 739, 740, 762, 763, 764,
            767, 772, 779, 942, 950, 953, 955, 958
        ),
        HarmCategory.INFRASTRUCTURE_RISK: (
            344, 563, 570, 601, 602, 603, 604, 605, 606, 607, 608, 609, 610, 611, 612, 613, 614, 615, 616, 617, 618,
            619, 620, 621, 623, 628, 631, 634, 636, 640, 687, 691, 698, 699, 1141
        ),
        HarmCategory.GAMBLING: (244,),
        HarmCategory.PUBLIC_HEALTH: (
            881, 882, 883, 884, 885, 886, 887, 888, 889, 890, 891, 892, 893, 894, 895, 896, 897, 898, 899, 900, 901,
            902, 903, 904, 905, 906, 907, 908, 909, 910, 911, 912, 913, 914, 915, 916, 917, 918, 919, 920, 921, 922,
            923, 924, 925, 926, 927, 928, 929, 930, 931, 932, 933, 934, 935, 936, 937, 938, 939, 940, 941, 943, 944,
            946, 947, 951, 952, 954, 956, 959, 960
        ),
        HarmCategory.OTHER: (
            121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 163,
            221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240, 261,
            262, 263, 264, 265, 266, 267, 268, 284, 286, 290, 293, 294, 295, 296, 297, 298, 299, 300, 310, 312, 314,
            316, 318, 320, 341, 342, 343, 346, 347, 381, 384, 518, 625, 626, 627, 630, 632, 633, 635, 637, 639, 641,
            642, 644, 647, 648, 651, 653, 655, 658, 660, 681, 683, 684, 686, 688, 689, 690, 695, 696, 697, 711, 712,
            714, 715, 716, 717, 718, 719, 720, 723, 724, 741, 742, 743, 744, 745, 746, 747, 748, 749, 750, 751, 752,
            753, 754, 755, 756, 757, 758, 759, 760, 761, 766, 768, 769, 770, 771, 773, 774, 775, 776, 778, 780, 786,
            792, 793, 794, 795, 796, 797, 799, 800, 822, 823, 824, 825, 826, 827, 828, 829, 830, 831, 832, 833, 834,
            835, 836, 837, 838, 839, 840, 981, 982, 984, 985, 986, 987, 988, 989, 990, 991, 992, 993, 994, 996, 997,
            998, 1000, 1041, 1042, 1043, 1044, 1045, 1046, 1047, 1048, 1049, 1050, 1051, 1052, 1053, 1054, 1055,
            1056, 1057, 1058, 1059, 1060, 1061, 1062, 1063, 1064, 1065, 1066, 1067, 1068, 1069, 1070, 1071, 1072,
            1073, 1074, 1075, 1076, 1077, 1078, 1079, 1080, 1081, 1082, 1084, 1085, 1087, 1089, 1090, 1091, 1092,
            1093, 1094, 1095, 1096, 1097, 1098, 1099, 1101, 1102, 1103, 1104, 1105, 1106, 1107, 1108, 1109, 1110,
            1111, 1112, 1113, 1114, 1115, 1116, 1117, 1118, 1119, 1120, 1121, 1122, 1123, 1124, 1125, 1126, 1127,
            1128, 1129, 1130, 1131, 1132, 1133, 1134, 1135, 1136, 1137, 1138, 1139, 1140, 1161, 1162, 1164, 1166,
            1167, 1168, 1169, 1170, 1171, 1174, 1176, 1177, 1178, 1180, 1221, 1222, 1223, 1224, 1225, 1226, 1227,
            1228, 1229, 1230, 1231, 1232, 1233, 1234, 1235, 1236, 1237, 1238, 1239, 1240, 1352, 1357, 1362, 1363,
            1364, 1366, 1367, 1368, 1370, 1371, 1372, 1375, 1376, 1378, 1381, 1382, 1383, 1384, 1385, 1386, 1387,
            1388, 1389, 1390, 1391, 1392, 1393, 1394, 1395, 1396, 1397, 1398, 1399, 1400, 1408, 1409, 1411, 1413,
            1416, 1418, 1420, 1441, 1447, 1448, 1449, 1451, 1452, 1453, 1454, 1455, 1456, 1457, 1460, 1514, 1518,
            1521, 1522, 1523, 1524, 1525, 1526, 1527, 1528, 1529, 1530, 1531, 1532, 1533, 1534, 1535, 1536, 1537,
            1538, 1539, 1540, 1543, 1602, 1623, 1625, 1628, 1629, 1631, 1633, 1634, 1635, 1637, 1638, 1639, 1742,
            1743, 1746, 1747, 1749, 1750, 1754, 1755, 1756, 1757, 1758, 1759, 1760, 1782, 1783, 1784, 1787, 1788,
            1789, 1791, 1792, 1793, 1794, 1795, 1796, 1797, 1798, 1799, 1800, 1841, 1842, 1846, 1847, 1848, 1850,
            1851, 1853, 1854, 1857, 1859, 1860, 1881, 1884, 1885, 1890, 1896, 1900, 1941, 1942, 1943, 1944, 1945,
            1946, 1947, 1948, 1949, 1950, 1951, 1952, 1953, 1954, 1955, 1956, 1957, 1958, 1959, 1960
        ),
    }
# fmt: on

# Flat index built once at import for O(1) per-row lookup.
_HARM_CATEGORY_BY_ID: dict[int, HarmCategory] = {
    row_id: category for category, ids in _IDS_BY_HARM_CATEGORY.items() for row_id in ids
}

# Guard against an id accidentally listed under two categories while editing the groups.
if sum(len(ids) for ids in _IDS_BY_HARM_CATEGORY.values()) != len(_HARM_CATEGORY_BY_ID):
    raise ValueError("harmful_qa: an id appears in more than one _IDS_BY_HARM_CATEGORY group")


class _HarmfulQADataset(_RemoteDatasetLoader):
    """
    Loader for the HarmfulQA dataset from HuggingFace.

    HarmfulQA contains approximately 2k harmful questions organized by academic topic
    and subtopic, designed to test LLM susceptibility to harm-inducing question-answering.

    References:
        - https://huggingface.co/datasets/declare-lab/HarmfulQA
        - [@bhardwaj2023harmfulqa]
    License: Apache 2.0

    Warning: This dataset contains harmful questions designed to test LLM safety.
    """

    HF_DATASET_NAME: str = "declare-lab/HarmfulQA"

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "large"  # 1960 harmful questions by academic topic
    tags: frozenset[str] = frozenset({"default", "safety", "jailbreak"})

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "harmful_qa"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HarmfulQA dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HarmfulQA questions.
        """
        logger.info(f"Loading HarmfulQA dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
        )

        authors = [
            "Rishabh Bhardwaj",
            "Soujanya Poria",
        ]
        description = (
            "HarmfulQA contains ~2k harmful questions organized by academic topic and subtopic, "
            "designed to test LLM susceptibility to harm-inducing question-answering. Introduced "
            "in 'Red-Teaming Large Language Models using Chain of Utterances for Safety Alignment' (2023)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["DeCLaRe Lab, Singapore University of Technology and Design"]

        unmapped = 0
        seed_prompts: list[SeedUnion] = []
        for item in data:
            question = item["question"]
            topic = item.get("topic")
            subtopic = item.get("subtopic")

            raw_id = item.get("id")
            category = _HARM_CATEGORY_BY_ID.get(int(raw_id)) if raw_id is not None else None
            if category is not None:
                harm_categories = self._standardize_harm_categories([category])
            else:
                # id absent from the audited map (e.g. upstream added rows). Fall back to
                # the coarse subject mapping rather than mislabel.
                unmapped += 1
                harm_categories = self._standardize_harm_categories(topic)

            metadata: dict[str, str | int] = {}
            if topic:
                metadata["topic"] = topic
            if subtopic:
                metadata["subtopic"] = subtopic

            seed_prompts.append(
                SeedPrompt(
                    value=question,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata=metadata,
                )
            )

        if unmapped:
            logger.warning(
                "%d HarmfulQA question(s) had an id that is absent from the audited row-level "
                "harm-category map and fell back to the coarse topic mapping. The upstream dataset "
                "may have added rows; regenerate _IDS_BY_HARM_CATEGORY to restore full coverage.",
                unmapped,
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} questions from HarmfulQA dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
