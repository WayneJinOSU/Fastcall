{
    "action": "start",
    "data": {
        "callee": "0",
        "agentID": "agenttest01979c07d989714fac0087f21ba8ce78",
        "businessID": "",
        "config": {
            "code": "01979c07d989714fac0087f21ba8ce78",
            "name": "qiansong_test_COPY",
            "orgCode": "66e18cf28eb54af88d2053c9d0a1a037",
            "orgName": "Mexico_Agent",
            "status": 1,
            "robotType": 1,
            "desc": "",
            "asr": {
                "code": "voiceagentstt00001",
                "locale": "zh-CN",
                "name": "中文",
                "provider": "deepgram",
            },
            "tts": {
                "code": "voiceagenttts00001",
                "locale": "zh-CN",
                "name": "7a5d4663-88ae-47b7-808e-8f9b9ee4127b",
                "provider": "cartesia",
                "model": "sonic-2",
                "styles": "",
                "roles": "",
            },
            "variables": {},
            "modelConfig": {
                "code": "2d0a13761aef42f2a074517b1592dce4",
                "type": "",
                "provider": "azure",
                "name": "gpt-4o",
                "url": "",
                "apiKey": "",
                "config": {"temperature": 0, "contextRounds": 10, "maxTokens": 100},
            },
            "promptConfig": {
                "content": "你是一名专业的贷款催收员。你需要向借款人催收欠款, 你输出{{language}}文字。\\n\\n今天是约定还款日。开场要简明扼要,先确认身份,然后直接表明来意。\\n\\n保持专业且有一定压迫感的语气。\\n若用户提问无关信息，请强调当前对话催收重点。\\n\\n\\n简短介绍自己来自{{app_type}}并确认用户信息,直接询问其还款意向。  \\n请注意你的 SOP 是：\\n1. 简短介绍自己来自{{app_type}}，无需介绍姓名\\n2. 确认用户信息 (例如：{{user_name}}，您好)\\n3. 说明其欠款平台{{app_type}}和金额信息{{remaining_amount}}，并询问其还款意向\\n4. 若用户准备延迟还款，告知付款必须今天按时完成，最迟在今天墨西哥城时间上午十点之前，不接受下午或者明天等日期延迟还款\\n5. 用户有其他接口或推脱，告知你没有责任考虑这些，因为用户已经同意{{app_type}}应用程序签署的条款\\n6. 用户若说明通过某些还款方式还款失败，请告知用户可以尝试其他方式比如直接银行转账\\n7. 用户若说没有收到过钱，请说明目前拥有详细的用户信息，不可能发生这种情况，强硬表明还款才能解决后续不必要麻烦（用户在欺骗和找借口）\\n8. 若用户表明已还款或者已经退回贷款，说明目前没有确认到这些操作，请用户通过WhatsApp提供付款证明\\n9. 你没有查询功能，若用户询问相关信息直接告知其下面贷款信息:\\n    贷款平台：{{app_type}}\\n    贷款金额：{{remaining_amount}}\\n    还款日：今天 ({{datetime}})\\n\\n注意：\\n上面SOP步骤 1-2 执行完等用户回复确认后，再执行步骤 3，不要一次执行过多步骤，尽量简短对话\\n若对方可能是语音信箱，请留下必要催收信息后，再调用end_the_conversation方法结束对话\\n\\n\\n关键信息：\\n今天日期：{{datetime}}\\n\\n客户信息：\\n1. 姓名：{{user_name}}\\n2. 贷款金额：{{remaining_amount}}"
            },
            "flowConfig": {
                "welcomeNode": {
                    "content": "请问是 {{user_name}}吗，今天是您的还款日，请问你有什么还款计划？",
                    "timeoutSec": 5,
                    "timeoutReply": "您好，还在吗？及时还款可以避免罚息以及下次快速通过下次贷款。",
                },
                "endNode": {
                    "content": "好的，感谢您的时间，再见！",
                    "hangUpConfig": {
                        "delayHangUp": {"enabled": True, "delaySecond": 2},
                        "otherHangUp": {
                            "minInteractions": 15,
                            "minCallDurationSec": 300,
                            "content": "好的，感谢您的时间，再见！",
                        },
                    },
                },
                "noResponse": {
                    "timeoutSec": 5,
                    "noResponseReply": "请问您还在吗？",
                    "maxNoResponseCount": 3,
                    "noResponseHangUp": {
                        "enabled": True,
                        "hangupMessage": "感谢你的接听，让我们下次再联系，再见",
                    },
                },
            },
        },
    },
}

# call_data
{
    "action": "start",
    "data": {
        "callUUID": "2d1c7c66-8193-4463-abb8-bbc8387ff77f",
        "callee": "0",
        "agentID": "agenttest01979c07d989714fac0087f21ba8ce78",
        "businessID": "",
        "config": {
            "code": "01979c07d989714fac0087f21ba8ce78",
            "name": "qiansong_test_COPY",
            "orgCode": "66e18cf28eb54af88d2053c9d0a1a037",
            "orgName": "Mexico_Agent",
            "status": 1,
            "robotType": 1,
            "desc": "",
            "asr": {
                "code": "voiceagentstt00001",
                "locale": "zh-CN",
                "name": "中文",
                "provider": "deepgram",
            },
            "tts": {
                "code": "voiceagenttts00001",
                "locale": "zh-CN",
                "name": "7a5d4663-88ae-47b7-808e-8f9b9ee4127b",
                "provider": "cartesia",
                "model": "sonic-2",
                "styles": "",
                "roles": "",
            },
            "variables": {},
            "modelConfig": {
                "code": "2d0a13761aef42f2a074517b1592dce4",
                "type": "",
                "provider": "azure",
                "name": "gpt-4o",
                "url": "",
                "apiKey": "",
                "config": {"temperature": 0, "contextRounds": 10, "maxTokens": 100},
            },
            "promptConfig": {
                "content": "你是一名专业的贷款催收员。你需要向借款人催收欠款, 你输出中文文字。\\n\\n今天是约定还款日。开场要简明扼要,先确认身份,然后直接表明来意。\\n\\n保持专业且有一定压迫感的语气。\\n若用户提问无关信息，请强调当前对话催收重点。\\n\\n\\n简短介绍自己来自super并确认用户信息,直接询问其还款意向。  \\n请注意你的 SOP 是：\\n1. 简短介绍自己来自super，无需介绍姓名\\n2. 确认用户信息 (例如：小曹，您好)\\n3. 说明其欠款平台super和金额信息1000，并询问其还款意向\\n4. 若用户准备延迟还款，告知付款必须今天按时完成，最迟在今天墨西哥城时间上午十点之前，不接受下午或者明天等日期延迟还款\\n5. 用户有其他接口或推脱，告知你没有责任考虑这些，因为用户已经同意super应用程序签署的条款\\n6. 用户若说明通过某些还款方式还款失败，请告知用户可以尝试其他方式比如直接银行转账\\n7. 用户若说没有收到过钱，请说明目前拥有详细的用户信息，不可能发生这种情况，强硬表明还款才能解决后续不必要麻烦（用户在欺骗和找借口）\\n8. 若用户表明已还款或者已经退回贷款，说明目前没有确认到这些操作，请用户通过WhatsApp提供付款证明\\n9. 你没有查询功能，若用户询问相关信息直接告知其下面贷款信息:\\n    贷款平台：super\\n    贷款金额：1000\\n    还款日：今天 (2025-06-26)\\n\\n注意：\\n上面SOP步骤 1-2 执行完等用户回复确认后，再执行步骤 3，不要一次执行过多步骤，尽量简短对话\\n若对方可能是语音信箱，请留下必要催收信息后，再调用end_the_conversation方法结束对话\\n\\n\\n关键信息：\\n今天日期：2025-06-26\\n\\n客户信息：\\n1. 姓名：小曹\\n2. 贷款金额：1000"
            },
            "flowConfig": {
                "welcomeNode": {
                    "content": "请问是 小曹吗，今天是您的还款日，请问你有什么还款计划？",
                    "timeoutSec": 5,
                    "timeoutReply": "您好，还在吗？及时还款可以避免罚息以及下次快速通过下次贷款。",
                },
                "endNode": {
                    "content": "好的，感谢您的时间，再见！",
                    "hangUpConfig": {
                        "delayHangUp": {"enabled": True, "delaySecond": 2},
                        "otherHangUp": {
                            "minInteractions": 15,
                            "minCallDurationSec": 300,
                            "content": "好的，感谢您的时间，再见！",
                        },
                    },
                },
                "noResponse": {
                    "timeoutSec": 5,
                    "noResponseReply": "请问您还在吗？",
                    "maxNoResponseCount": 3,
                    "noResponseHangUp": {
                        "enabled": True,
                        "hangupMessage": "感谢你的接听，让我们下次再联系，再见",
                    },
                },
            },
        },
    },
}


{
    "action": "start",
    "data": {
        "callUUID": "cf1245f7-7b05-4adf-8b30-006cab37c2f1",
        "callee": "0",
        "agentID": "agenttest0197ab2e92d07607bf9fc8c4ad7c5a42",
        "businessID": "",
        "config": {
            "code": "0197ab2e92d07607bf9fc8c4ad7c5a42",
            "name": "qiansong_test",
            "orgCode": "f10a102643ac47dbabf3b29c17d4924d",
            "orgName": "Test",
            "status": 1,
            "robotType": 1,
            "desc": "",
            "asr": {
                "code": "c5a1d84fa71741349a244941b9dd22cd",
                "locale": "zh-CN",
                "name": "中文",
                "provider": "deepgram",
            },
            "tts": {
                "code": "58926440999d4ce985fc20a15d44534c",
                "locale": "zh-CN",
                "name": "7a5d4663-88ae-47b7-808e-8f9b9ee4127b",
                "provider": "cartesia",
                "model": "sonic-2",
                "styles": "",
                "roles": "",
            },
            "variables": {},
            "modelConfig": {
                "code": "2d0a13761aef42f2a074517b1592dce4",
                "type": "",
                "provider": "openrouter",
                "name": "openai/gpt-4o",
                "url": "",
                "apiKey": "",
                "config": {"temperature": 0.2, "contextRounds": 10, "maxTokens": 100},
            },
            "promptConfig": {
                "content": "你是一名专业的贷款催收员。你需要向借款人催收欠款, 你输出中文文字。\\n\\n今天是约定还款日。开场要简明扼要,先确认身份,然后直接表明来意。\\n\\n保持专业且有一定压迫感的语气。\\n若用户提问无关信息，请强调当前对话催收重点。\\n\\n\\n简短介绍自己来自palpla并确认用户信息,直接询问其还款意向。  \\n请注意你的 SOP 是：\\n1. 简短介绍自己来自palpla，无需介绍姓名\\n2. 确认用户信息 (例如：Wayne Wang，您好)\\n3. 说明其欠款平台palpla和金额信息{{remaining_amount}}，并询问其还款意向\\n4. 若用户准备延迟还款，告知付款必须今天按时完成，最迟在今天墨西哥城时间上午十点之前，不接受下午或者明天等日期延迟还款\\n5. 用户有其他接口或推脱，告知你没有责任考虑这些，因为用户已经同意palpla应用程序签署的条款\\n6. 用户若说明通过某些还款方式还款失败，请告知用户可以尝试其他方式比如直接银行转账\\n7. 用户若说没有收到过钱，请说明目前拥有详细的用户信息，不可能发生这种情况，强硬表明还款才能解决后续不必要麻烦（用户在欺骗和找借口）\\n8. 若用户表明已还款或者已经退回贷款，说明目前没有确认到这些操作，请用户通过WhatsApp提供付款证明\\n9. 你没有查询功能，若用户询问相关信息直接告知其下面贷款信息:\\n    贷款平台：palpla\\n    贷款金额：{{remaining_amount}}\\n    还款日：今天 (2025-01-01 12:00:00)\\n\\n注意：\\n上面SOP步骤 1-2 执行完等用户回复确认后，再执行步骤 3，不要一次执行过多步骤，尽量简短对话\\n若对方可能是语音信箱，请留下必要催收信息后，再调用end_the_conversation方法结束对话\\n\\n\\n关键信息：\\n今天日期：2025-01-01 12:00:00\\n\\n客户信息：\\n1. 姓名：Wayne Wang\\n2. 贷款金额：{{remaining_amount}}"
            },
            "flowConfig": {
                "welcomeNode": {
                    "content": "请问是 Wayne Wang吗，今天是您的还款日，请问你有什么还款计划吗",
                    "timeoutSec": 5,
                    "timeoutReply": "您好，还在吗？及时还款可以避免罚息以及下次快速通过下次贷款。",
                },
                "endNode": {
                    "content": "好的，感谢您的时间，再见！",
                    "hangUpConfig": {
                        "delayHangUp": {"enabled": True, "delaySecond": 2},
                        "otherHangUp": {
                            "minInteractions": 15,
                            "minCallDurationSec": 300,
                            "content": "好的，感谢您的时间。",
                        },
                    },
                },
                "noResponse": {
                    "timeoutSec": 5,
                    "noResponseReply": "请问您还在吗？",
                    "maxNoResponseCount": 3,
                    "noResponseHangUp": {
                        "enabled": True,
                        "hangupMessage": "感谢你的接听，让我们下次再联系，再见",
                    },
                },
            },
        },
    },
}


{
    "action": "start",
    "data": {
        "callUUID": "2e49a6c2-51c7-41d8-9112-487b28645c86",
        "callee": "0",
        "agentID": "agenttest0197ab2e92d07607bf9fc8c4ad7c5a42",
        "businessID": "",
        "config": {
            "code": "0197ab2e92d07607bf9fc8c4ad7c5a42",
            "name": "qiansong_test",
            "orgCode": "f10a102643ac47dbabf3b29c17d4924d",
            "orgName": "Test",
            "status": 1,
            "robotType": 1,
            "desc": "",
            "asr": {
                "code": "c5a1d84fa71741349a244941b9dd22cd",
                "locale": "zh-CN",
                "name": "中文",
                "provider": "deepgram",
            },
            "tts": {
                "code": "58926440999d4ce985fc20a15d44534c",
                "locale": "zh-CN",
                "name": "7a5d4663-88ae-47b7-808e-8f9b9ee4127b",
                "provider": "cartesia",
                "model": "sonic-2",
                "styles": "",
                "roles": "",
            },
            "variables": {},
            "modelConfig": {
                "code": "2d0a13761aef42f2a074517b1592dce4",
                "type": "",
                "provider": "openrouter",
                "name": "openai/gpt-4o",
                "url": "",
                "apiKey": "",
                "config": {"temperature": 0.2, "contextRounds": 10, "maxTokens": 100},
            },
            "promptConfig": {
                "content": "你是一名专业的贷款催收员。你需要向借款人催收欠款, 你输出中文文字。\\n\\n今天是约定还款日。开场要简明扼要,先确认身份,然后直接表明来意。\\n\\n保持专业且有一定压迫感的语气。\\n若用户提问无关信息，请强调当前对话催收重点。\\n\\n\\n简短介绍自己来自palpla并确认用户信息,直接询问其还款意向。  \\n请注意你的 SOP 是：\\n1. 简短介绍自己来自palpla，无需介绍姓名\\n2. 确认用户信息 (例如：Wayne Wang，您好)\\n3. 说明其欠款平台palpla和金额信息{{remaining_amount}}，并询问其还款意向\\n4. 若用户准备延迟还款，告知付款必须今天按时完成，最迟在今天墨西哥城时间上午十点之前，不接受下午或者明天等日期延迟还款\\n5. 用户有其他接口或推脱，告知你没有责任考虑这些，因为用户已经同意palpla应用程序签署的条款\\n6. 用户若说明通过某些还款方式还款失败，请告知用户可以尝试其他方式比如直接银行转账\\n7. 用户若说没有收到过钱，请说明目前拥有详细的用户信息，不可能发生这种情况，强硬表明还款才能解决后续不必要麻烦（用户在欺骗和找借口）\\n8. 若用户表明已还款或者已经退回贷款，说明目前没有确认到这些操作，请用户通过WhatsApp提供付款证明\\n9. 你没有查询功能，若用户询问相关信息直接告知其下面贷款信息:\\n    贷款平台：palpla\\n    贷款金额：{{remaining_amount}}\\n    还款日：今天 (2025-01-01 12:00:00)\\n\\n注意：\\n上面SOP步骤 1-2 执行完等用户回复确认后，再执行步骤 3，不要一次执行过多步骤，尽量简短对话\\n若对方可能是语音信箱，请留下必要催收信息后，再调用end_the_conversation方法结束对话\\n\\n\\n关键信息：\\n今天日期：2025-01-01 12:00:00\\n\\n客户信息：\\n1. 姓名：Wayne Wang\\n2. 贷款金额：{{remaining_amount}}"
            },
            "flowConfig": {
                "welcomeNode": {
                    "content": "请问是 Wayne Wang吗，今天是您的还款日，请问你有什么还款计划吗",
                    "timeoutSec": 5,
                    "timeoutReply": "您好，还在吗？及时还款可以避免罚息以及下次快速通过下次贷款。",
                },
                "endNode": {
                    "content": "好的，感谢您的时间，再见！",
                    "hangUpConfig": {
                        "delayHangUp": {"enabled": True, "delaySecond": 2},
                        "otherHangUp": {
                            "minInteractions": 15,
                            "minCallDurationSec": 300,
                            "content": "好的，感谢您的时间。",
                        },
                    },
                },
                "noResponse": {
                    "timeoutSec": 5,
                    "noResponseReply": "请问您还在吗？",
                    "maxNoResponseCount": 3,
                    "noResponseHangUp": {
                        "enabled": True,
                        "hangupMessage": "感谢你的接听，让我们下次再联系，再见",
                    },
                },
            },
        },
    },
}
