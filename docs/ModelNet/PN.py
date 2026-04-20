import requests
import math
import random
import time
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import utils
from openai import OpenAI
import pdb

# 获取配置好的日志记录器
logger = utils.setup_logger()

class MultiModelHandler:
    def __init__(self, file_path = None, max_workers=10):
        """
        初始化多模型处理器。
        :param model_info: 模型的信息
        :param max_workers: 并发线程数
        """
        # 读取模型配置文件
        with open(file_path, 'r', encoding='utf-8') as file:
            self.data = json.load(file)

        # 设置模型并行处理数量
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        # 显示相关信息
        logger.info(f'max_workers:{max_workers}')
        logger.info('Model list:\n' + '\n'.join([f"id: {item['id']}, model_name: {item['model_name']}" for item in self.data]))

    def generate_response(self, model_choice, question, args):
        """
        生成最终回答。
        :param model_choice: 选择的模型编号列表
        :param question: 输入问题
        :param args: 参数配置
        :return: 最终生成的答案
        """
        
        # 通过数据库获取模型信息
        self.models_info =[self.data[i] for i in model_choice]
        logger.info('selected models:\n' + '\n'.join([f"id: {item['id']}, model_name: {item['model_name']}" for item in self.models_info]))

        # 初始化temple化字符串
        texts = [None] * len(self.models_info)

        # 利用对应路由服务进行模板化
        for i, item in enumerate(self.models_info):
            texts[i] = self.call_template(question, item)

        # 让思考模型并行思考完成之后进行协作
        think_task = [self.executor.submit(self.process_think_task, i, texts[i], info) for i,info in enumerate(self.models_info)]
        for future in as_completed(think_task):
            result = future.result()
            if result[1] is not None:
                texts[result[0]] += result[1]
  

        # 初始化下一词汇、总回答内容、回答长度
        new_word, ans, len_of_tokens = "", "", 0


        time1 = time.time()
        excu_time = 0
        while new_word not in ['<end>']:

            # 设置长度约束
            if len_of_tokens > args['max_len']:
                break
            else:
                len_of_tokens += 1

            # 初始化词汇预测集合
            next_words = {}
        
            # 并发调用模型
            all_task = [self.executor.submit(self.call_app, i, texts[i], info, args) for i,info in enumerate(self.models_info)]

            # 提取模型结果
            for future in as_completed(all_task):
                result = future.result()
                topk_token = sorted(result[1]['prediction_values'], key=lambda x: x[1], reverse=True)[:args['top_k']]
                next_words[result[0]] = topk_token
                
            # 预测结果聚合器，用来确定最佳的结果
            start = time.time()
            new_word, _ = self.calculate_scores(next_words, args)
            end = time.time()
            excu_time += (end - start)
                
            # 把文本添加到模板中
            for i, text in enumerate(texts):
                texts[i] += new_word

            # 把文本添加到答案中
            ans += new_word

        time2 = time.time()
        logger.info(f"总耗时比为{len_of_tokens / (time2-time1) }")

        # 并行清理kv缓存
        # all_task = [self.executor.submit(self.clear_slot_kv_cache, 0, info['model_url']) for i,info in enumerate(self.models_info)]
        
        return ans, len_of_tokens, time2 - time1, excu_time

    def clear_slot_kv_cache(self, slot_id=0, base_url="http://localhost:8080"):
        """
        清理指定 Slot 的 KV 缓存
        
        Args:
            slot_id: 要清理的 Slot ID
            base_url: API 服务器地址
        """
        url = f"{base_url}/slots/{slot_id}?action=erase"
        try:
            response = requests.post(url)
            
            if response.status_code == 200:
                return True
            else:
                print(f"清理失败，状态码: {response.status_code}")
                print(f"响应内容: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"请求异常: {e}")
            return False

    def call_app(self, i, text, info, args):
        """
        调用单个模型的预测服务。
        :param i: 模型次序
        :param text: 输入文本
        :param info: 模型相关信息
        :param args: 模型推理参数
        :return: (i, 模型预测结果)
        """
        
        # 调用llama.cpp的模型服务
        url = f"{info['model_url']}/completion"
        headers = {'Content-Type': 'application/json'}
        data = {'prompt': text} | args
        response = requests.post(url, json=data, headers=headers)

        # 根据调用结果读取相关信息
        result = {'prediction_values': [], 'args': {},  'sample_result': []}

        # 假如调用出错则直接返回结果
        if response.status_code != 200:
            logger.info(f"Error: {response.json()['error']['message']}")
            result['prediction_values'].append(['<end>', 0.01])
            return i,result

        # 处理乱码逻辑
        while ('completion_probabilities' not in response.json().keys()) and (data['max_tokens'] < 10):
            data['max_tokens'] = data['max_tokens'] + 1
            response = requests.post(url, json=data, headers=headers)
            if response.status_code != 200:
                logger.info(f"Error: {response.json()['error']['message']}")
                result['prediction_values'].append(['<end>', 0.01])
                return i,result


        # 提取信息
        res_probs = response.json()['completion_probabilities'][0]['top_probs']
        result['sample_result'].append(response.json()['content'])
        result['args'] = response.json()['generation_settings']

        for item in res_probs:
            token = item['token']

            #处理终止符逻辑
            if token in ["", info['EOS']]:
                token = '<end>'
            logprob = item['prob']
            bytes = item['bytes']
            result['prediction_values'].append([token, logprob])

        return i, result


    def process_think_task(self, i, text, model_info):

        """处理单个思考任务的函数"""
        if model_info['type'] == "think":
            # 若为思考模型，则让模型先思考再回答
            url = f"{model_info['model_url']}/completion"
            headers = {'Content-Type': 'application/json'}
            data = {
                'prompt': text, 
                'stop': [model_info['stop_think']],
                'max_tokens': 8196
                }

            response = requests.post(url, json=data, headers=headers)
            think_text = response.json()['content'] + model_info['stop_think']
            logger.info(f"模型{model_info['model_name']}:思考完成")
            return i, think_text
        else:
            # 若不为思考模型，则直接输出
            return i, None

    

    def call_template(self, question, info):
        """
        调用单个模型的模板生成服务。

        :param question: 输入问题
        :param port: 服务端口
        :return: 模板文本
        """

        # 调用模板生成
        url = f"{info['model_url']}/apply-template"
        headers = {'Content-Type': 'application/json'}
        data = {'messages': [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": question}]}

        response = requests.post(url, data=json.dumps(data), headers=headers)

        if response.status_code == 200:
            return response.json().get('prompt', '')
        else:
            logger.info(f"Error: {response.status_code}")
            return ""

    def calculate_scores(self, data, args):
        """
        根据模型输出计算最终的选择。

        :param data: 模型输出数据
        :param args: 参数配置（包括模式选择）
        :return: （选定的词，得分）
        """
 
        scores = {}
        for _, values in data.items():
            for word, score in values:
                scores[word] = scores.get(word, 0) + score

        if not scores:
            return "<end>", 1.0

        max_score = max(scores.values())
        highest_scoring_words = [word for word, score in scores.items() if score == max_score]
        return random.choice(highest_scoring_words), max_score
    

# 使用示例
if __name__ == '__main__':
    
    # 实例化协作器
    handler = MultiModelHandler(file_path="model_info.json")

    # 设置模型选项
    model_choice = [0]
    
    # 设置提示词
    question = '你知道智谱团队吗？'

    # 设置参数
    args = {
        'top_k': 5,
        'max_len': 1000,
        'n_probs': 5, 
        'max_tokens': 1,
        'post_sampling_probs' : True,
        # "cache_prompt": True,
        # "id_slot": 0 
    }              

    # 调用生成答案
    result = handler.generate_response(model_choice, question, args)
    logger.info(f"回答: {result}")
