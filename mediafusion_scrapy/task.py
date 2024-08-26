from multiprocessing import Process

import dramatiq
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings


def run_spider_in_process(spider_name, *args, **kwargs):
    """
    Function to start a scrapy spider in a new process.
    """
    process = CrawlerProcess(get_project_settings())
    process.crawl(spider_name, *args, **kwargs)
    process.start()


@dramatiq.actor(priority=5, time_limit=60 * 60 * 1000, queue_name="scrapy")
def run_spider(spider_name: str, *args, **kwargs):
    """
    Wrapper function to run the spider in a separate process.
    """
    p = Process(target=run_spider_in_process, args=(spider_name, *args), kwargs=kwargs)
    p.start()
    p.join()


if __name__ == "__main__":
    run_spider_in_process("streamed")
