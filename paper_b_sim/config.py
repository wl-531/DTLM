TRACE_DIR = r"D:\data\Azure Functions Trace"
OUTPUT_DIR = r"D:\code\paper_b_sim\results"
SEED = 42

# 抽样参数
N_APPS = 50
SMALL_MEM_THRESHOLD = 70   # MB
LARGE_MEM_THRESHOLD = 700  # MB
N_SMALL = 1
N_LARGE = 1

# 默认实验参数
M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]  # M / working_set
