# Software Requirements Specification

## for AI-Powered Customer Support Platform

**Version:** 1.0  
**Prepared by:** Person A  
**Organization:** Company A  
**Date created:** 2026-11-16

---

> **Privacy:** personal and identifying data in this document (names, e-mail addresses, company, tool and place names, e-mail timestamps) has been replaced with consistent pseudonyms such as "Person A" and "Company A".

## Table of Contents

- [1. Introduction](#sec-1)
  - [1.1. Purpose](#sec-1-1)
  - [1.2. Document Conventions](#sec-1-2)
  - [1.3. Intended Audience and Reading Suggestions](#sec-1-3)
  - [1.4. Product Scope](#sec-1-4)
  - [1.5. References](#sec-1-5)
- [2. Overall Description](#sec-2)
  - [2.1. Product Perspective](#sec-2-1)
  - [2.2. Product Features](#sec-2-2)
  - [2.3. User Classes and Characteristics](#sec-2-3)
  - [2.4. Operating Environment](#sec-2-4)
  - [2.5. Design and Implementation Constraints](#sec-2-5)
  - [2.6. User Documentation](#sec-2-6)
  - [2.7. Assumptions and Dependencies](#sec-2-7)
- [3. System Features](#sec-3)
  - [3.1. Channel Integration](#sec-3-1)
  - [3.2. Customer Identification](#sec-3-2)
  - [3.3. Case Management](#sec-3-3)
  - [3.4. Service Level Monitoring](#sec-3-4)
  - [3.5. Automated Messaging](#sec-3-5)
  - [3.6. Workflow Automation](#sec-3-6)
  - [3.7. AI Assistance](#sec-3-7)
- [4. External Interface Requirements](#sec-4)
  - [4.1. User Interfaces Overview](#sec-4-1)
  - [4.2. Hardware Interfaces](#sec-4-2)
  - [4.3. Software Interfaces](#sec-4-3)
- [5. System Features/Modules](#sec-5)
  - [5.1. Channel Integration](#sec-5-1)
    - [5.1.1. Description and Priority](#sec-5-1-1)
    - [5.1.2. Stimulus/Response Sequences](#sec-5-1-2)
    - [5.1.3. Functional Requirements](#sec-5-1-3)
  - [5.2. Customer Identification](#sec-5-2)
    - [5.2.1. Description and Priority](#sec-5-2-1)
    - [5.2.2. Stimulus/Response Sequences](#sec-5-2-2)
    - [5.2.3. Functional Requirements](#sec-5-2-3)
  - [5.3. Case Management](#sec-5-3)
    - [5.3.1. Description and Priority](#sec-5-3-1)
    - [5.3.2. Stimulus/Response Sequences](#sec-5-3-2)
    - [5.3.3. Functional Requirements](#sec-5-3-3)
  - [5.4. Service Level Monitoring](#sec-5-4)
    - [5.4.1. Description and Priority](#sec-5-4-1)
    - [5.4.2. Stimulus/Response Sequences](#sec-5-4-2)
    - [5.4.3. Functional Requirements](#sec-5-4-3)
  - [5.5. Automated Messaging](#sec-5-5)
    - [5.5.1. Description and Priority](#sec-5-5-1)
    - [5.5.2. Stimulus/Response Sequences](#sec-5-5-2)
    - [5.5.3. Functional Requirements](#sec-5-5-3)
  - [5.6. Workflow Automation](#sec-5-6)
    - [5.6.1. Description and Priority](#sec-5-6-1)
    - [5.6.2. Stimulus/Response Sequences](#sec-5-6-2)
    - [5.6.3. Functional Requirements](#sec-5-6-3)
  - [5.7. AI Assistance](#sec-5-7)
    - [5.7.1. Description and Priority](#sec-5-7-1)
    - [5.7.2. Stimulus/Response Sequences](#sec-5-7-2)
    - [5.7.3. Functional Requirements](#sec-5-7-3)
- [6. Nonfunctional Requirements](#sec-6)
  - [6.1. Performance](#sec-6-1)
  - [6.2. Security](#sec-6-2)
- [Appendix A. Traceability Matrix](#appendix-a)
- [Appendix B. Sources](#appendix-b)
- [Appendix C. Validation Notes](#appendix-c)

## Revision History

| Name | Date | Reason for Changes | Version |
| --- | --- | --- | --- |
| Person A | 2026-11-16 | Initial version generated from approved requirements | 1.0 |

---

<a id="sec-1"></a>
## 1. Introduction

<a id="sec-1-1"></a>
### 1.1. Purpose

This document specifies the software requirements for a dedicated customer support platform for Company A. The platform consolidates customer communications from various online marketplaces and channels into a unified workspace, providing case management, workflow automation, and AI assistance. [S1](#src-1) [S2](#src-2)

<a id="sec-1-2"></a>
### 1.2. Document Conventions

Section 3 introduces each system feature and section 5 specifies it, with the same feature numbering (3.N and 5.N). Functional requirements are identified as REQ-N.M (feature N, requirement M) and nonfunctional requirements as NF-N.M (category N of section 6, requirement M). Every requirement carries its own MoSCoW priority (must, should, could, wont); a feature's priority in 5.N.1 is the highest priority among its functional requirements. Stimulus/response sequences are identified as SR-N.M. Source ids refer to passages of the source documents listed in 1.5 References. Sections marked unresolved are not covered by the source documents.

<a id="sec-1-3"></a>
### 1.3. Intended Audience and Reading Suggestions

This specification is intended for project stakeholders, including Person A, Person B, and Person C, as well as the support team members (such as Person D and Person E). Readers should begin with Section 1 and Section 2 for general background and system scope, consult Section 3 and Section 4 for feature overviews and external system interfaces, and review Section 5 and Section 6 for detailed functional and nonfunctional requirements. [S3](#src-3) [S4](#src-4) [S5](#src-5) [S6](#src-6) [S7](#src-7) [S8](#src-8)

<a id="sec-1-4"></a>
### 1.4. Product Scope

The platform aims to reduce repetitive manual tasks and enable the support team to resolve customer enquiries more quickly and consistently. It integrates communications across channels, provides unified case tracking with deadline monitoring, automates workflows such as complaint list generation and receipt confirmations, and delivers AI assistance to support agent workflows while maintaining controlled human approvals for refunds and replacements. [S9](#src-9) [S10](#src-10) [S11](#src-11) [S12](#src-12) [S13](#src-13) [S14](#src-14) [S15](#src-15)

<a id="sec-1-5"></a>
### 1.5. References

| ID | Title | Author | Version | Date | Location |
| --- | --- | --- | --- | --- | --- |
| REF-001 | source-document-1.pdf |  |  |  | input/source-document-1.pdf |

<a id="sec-2"></a>
## 2. Overall Description

<a id="sec-2-1"></a>
### 2.1. Product Perspective

The platform is a dedicated customer support platform proposed for Company A. It addresses current operational limitations and manual efforts associated with existing Helpdesk Tool X integrations by uniting support channels—including marketplaces, email, WhatsApp, and the Phone Agent Y AI phone agent—into a single workspace with integrated logistics and stock checks. [S2](#src-2) [S16](#src-16) [S17](#src-17) [S18](#src-18) [S19](#src-19) [S20](#src-20)

<a id="sec-2-2"></a>
### 2.2. Product Features

The platform includes Channel Integration across multiple marketplaces and messaging tools, automatic Customer Identification across channels, comprehensive Case Management, Service Level Monitoring to track response deadlines, Automated Messaging for incoming contacts, Workflow Automation for data extraction and complaints lists, and AI Assistance for draft replies, summaries, and verification checks. [S10](#src-10) [S11](#src-11) [S12](#src-12) [S13](#src-13) [S17](#src-17) [S21](#src-21) [S22](#src-22) [S23](#src-23) [S24](#src-24) [S25](#src-25) [S26](#src-26) [S27](#src-27) [S28](#src-28) [S29](#src-29) [S30](#src-30) [S31](#src-31) [S32](#src-32) [S33](#src-33)

<a id="sec-2-3"></a>
### 2.3. User Classes and Characteristics

The system users are members of the support team: Person D (working 5 days a week from Country A), Person E (working Monday, Tuesday, and Thursday half days), and Person C (working daily as needed, handling urgent cases and exceptions for refunds, replacements, and spare parts). [S34](#src-34) [S6](#src-6) [S7](#src-7) [S8](#src-8) [S33](#src-33)

<a id="sec-2-4"></a>
### 2.4. Operating Environment

> **Unresolved:** The source passages do not state the operating environment, hosting infrastructure, or supported client platforms.

<a id="sec-2-5"></a>
### 2.5. Design and Implementation Constraints

The AI assistance is strictly constrained from making autonomous decisions regarding refunds, replacements, or similar actions. In addition, response templates and policies must conform to the specific operational rules of each marketplace. [S32](#src-32) [S35](#src-35)

<a id="sec-2-6"></a>
### 2.6. User Documentation

> **Unresolved:** The source passages do not specify user documentation requirements or user manuals to be delivered.

<a id="sec-2-7"></a>
### 2.7. Assumptions and Dependencies

Support operations are closed on weekends and public holidays, resulting in higher enquiry volumes on Mondays and subsequent working days. External knowledge and reference assets include product manuals maintained in Dropbox, historical conversations stored in Helpdesk Tool X and email accounts, and an early-stage knowledge base built with Claude and Obsidian. In addition, a separate tool for other marketplaces is in progress by Person F. [S36](#src-36) [S31](#src-31) [S37](#src-37) [S38](#src-38) [S39](#src-39)

<a id="sec-3"></a>
## 3. System Features

<a id="sec-3-1"></a>
### 3.1. Channel Integration

Consolidates customer communications from marketplaces, email, messaging apps, and phone agents into a unified workspace. It allows support agents to monitor and handle all incoming inquiries centrally. [S2](#src-2) [S17](#src-17) [S18](#src-18) [S19](#src-19) [S20](#src-20)

<a id="sec-3-2"></a>
### 3.2. Customer Identification

Identifies and links customer profiles across different communication channels to prevent duplicate agent handling and provide context. [S21](#src-21) [S26](#src-26)

<a id="sec-3-3"></a>
### 3.3. Case Management

Provides ticket handling, team member assignment, categorization, internal documentation, seamless handovers, and prioritization for all support cases. [S22](#src-22) [S23](#src-23) [S24](#src-24) [S25](#src-25) [S40](#src-40) [S33](#src-33) [S15](#src-15)

<a id="sec-3-4"></a>
### 3.4. Service Level Monitoring

Tracks response times and marketplace-specific reply deadlines to ensure compliance with service levels. [S27](#src-27) [S41](#src-41) [S42](#src-42) [S43](#src-43) [S44](#src-44)

<a id="sec-3-5"></a>
### 3.5. Automated Messaging

Dispatches automated acknowledgement messages to customers upon contact receipt. [S28](#src-28)

<a id="sec-3-6"></a>
### 3.6. Workflow Automation

Automates routine backend tasks including daily complaint reporting and fetching external logistics and inventory data. [S45](#src-45) [S29](#src-29) [S46](#src-46) [S30](#src-30) [S31](#src-31)

<a id="sec-3-7"></a>
### 3.7. AI Assistance

Assists agents with enquiry handling, drafting responses, summarizing long threads, and verifying order or stock status without autonomously approving financial or fulfillment actions. [S32](#src-32) [S47](#src-47) [S48](#src-48) [S49](#src-49) [S50](#src-50) [S51](#src-51) [S52](#src-52) [S53](#src-53) [S11](#src-11)

<a id="sec-4"></a>
## 4. External Interface Requirements

<a id="sec-4-1"></a>
### 4.1. User Interfaces Overview

The platform provides a shared overview displaying waiting time and remaining reply deadlines per marketplace, alongside a single unified view per case that presents customer details, channel history, order data, tracking status, stock availability, warranty status, and internal notes. [S10](#src-10) [S44](#src-44) [S15](#src-15)

<a id="sec-4-2"></a>
### 4.2. Hardware Interfaces

> **Not applicable:** The source passages do not specify any hardware interfaces for the software platform.

<a id="sec-4-3"></a>
### 4.3. Software Interfaces

The platform interfaces with multiple external software systems: communication and marketplace channels including eBay, OTTO, Kaufland, Amazon EU, Amazon US, two email accounts, WhatsApp, and Temu; the Phone Agent Y AI phone agent; logistics portals for DPD and DHL; inventory systems including Warehouse System Z and Amazon Seller Central; and CSV export compatibility for uploading orders to Asana. [S14](#src-14) [S17](#src-17) [S18](#src-18) [S19](#src-19) [S20](#src-20) [S29](#src-29) [S30](#src-30) [S31](#src-31)

<a id="sec-5"></a>
## 5. System Features/Modules

<a id="sec-5-1"></a>
### 5.1. Channel Integration

<a id="sec-5-1-1"></a>
#### 5.1.1. Description and Priority

Consolidates customer communications from marketplaces, email, messaging apps, and phone agents into a unified workspace. It allows support agents to monitor and handle all incoming inquiries centrally.

**Priority:** must

<a id="sec-5-1-2"></a>
#### 5.1.2. Stimulus/Response Sequences

**SR-1.1** [S2](#src-2) [S17](#src-17)

- **Stimulus / condition:** Customer support communications are received from eBay, Kaufland, OTTO, Amazon EU, Amazon US, or the two connected email accounts.
- **Response:** The system ingests and displays the communications within the single support workspace.
- **Requirements:** [REQ-1.1](#req-1-1)

**SR-1.2** [S18](#src-18) [S19](#src-19) [S20](#src-20)

- **Stimulus / condition:** Customer communications arrive via WhatsApp or Temu, or support cases are processed by the Phone Agent Y AI phone agent.
- **Response:** The system integrates these communications and cases into the central customer support platform.
- **Requirements:** [REQ-1.2](#req-1-2), [REQ-1.3](#req-1-3), [REQ-1.4](#req-1-4)

<a id="sec-5-1-3"></a>
#### 5.1.3. Functional Requirements

<a id="req-1-1"></a>
**REQ-1.1:** The system shall integrate customer support communications from eBay, Kaufland, OTTO, Amazon EU, Amazon US, and two email accounts into a single workspace. [S2](#src-2) [S17](#src-17)

- **Priority:** must
- **Stimulus/response:** SR-1.1
- **Origin:** registry RQ-0013 ([trace](#trace-req-1-1))

<a id="req-1-2"></a>
**REQ-1.2:** The system shall integrate customer support communications from WhatsApp. [S18](#src-18)

- **Priority:** should
- **Stimulus/response:** SR-1.2
- **Origin:** registry RQ-0014 ([trace](#trace-req-1-2))

<a id="req-1-3"></a>
**REQ-1.3:** The system shall integrate customer support communications from Temu. [S19](#src-19)

- **Priority:** should
- **Stimulus/response:** SR-1.2
- **Origin:** registry RQ-0015 ([trace](#trace-req-1-3))

<a id="req-1-4"></a>
**REQ-1.4:** The system shall integrate customer support cases handled by the Phone Agent Y AI phone agent. [S20](#src-20)

- **Priority:** should
- **Stimulus/response:** SR-1.2
- **Origin:** registry RQ-0016 ([trace](#trace-req-1-4))

<a id="sec-5-2"></a>
### 5.2. Customer Identification

<a id="sec-5-2-1"></a>
#### 5.2.1. Description and Priority

Identifies and links customer profiles across different communication channels to prevent duplicate agent handling and provide context.

**Priority:** must

<a id="sec-5-2-2"></a>
#### 5.2.2. Stimulus/Response Sequences

**SR-2.1** [S21](#src-21) [S26](#src-26)

- **Stimulus / condition:** A customer contacts the support team across multiple communication channels.
- **Response:** The system automatically detects and links the customer records together across those channels.
- **Requirements:** [REQ-2.1](#req-2-1)

<a id="sec-5-2-3"></a>
#### 5.2.3. Functional Requirements

<a id="req-2-1"></a>
**REQ-2.1:** The system shall automatically detect and link the same customer across multiple communication channels. [S21](#src-21) [S26](#src-26)

- **Priority:** must
- **Stimulus/response:** SR-2.1
- **Origin:** registry RQ-0017 ([trace](#trace-req-2-1))

<a id="sec-5-3"></a>
### 5.3. Case Management

<a id="sec-5-3-1"></a>
#### 5.3.1. Description and Priority

Provides ticket handling, team member assignment, categorization, internal documentation, seamless handovers, and prioritization for all support cases.

**Priority:** must

<a id="sec-5-3-2"></a>
#### 5.3.2. Stimulus/Response Sequences

**SR-3.1** [S22](#src-22) [S23](#src-23) [S24](#src-24) [S15](#src-15)

- **Stimulus / condition:** A customer case is created or accessed in the system.
- **Response:** The system allows assignment to any team member regardless of channel, enforces selection of a category, supports internal notes, and provides a unified view containing customer information, communication channels, order details, tracking status, stock availability, warranty status, internal notes, and history.
- **Requirements:** [REQ-3.1](#req-3-1), [REQ-3.2](#req-3-2), [REQ-3.3](#req-3-3), [REQ-3.7](#req-3-7)

**SR-3.2** [S25](#src-25)

- **Stimulus / condition:** A team member takes over a case previously started by another team member.
- **Response:** The system enables seamless handover so the case can be continued without gaps.
- **Requirements:** [REQ-3.4](#req-3-4)

**SR-3.3** [S40](#src-40)

- **Stimulus / condition:** Customer cases are queued for handling.
- **Response:** The system prioritizes cases based on longest waiting time regardless of topic.
- **Requirements:** [REQ-3.5](#req-3-5)

**SR-3.4** [S33](#src-33)

- **Stimulus / condition:** An exception arises regarding a refund, replacement, or spare part.
- **Response:** The system routes the exception to Person C for direct handling or reassignment.
- **Requirements:** [REQ-3.6](#req-3-6)

<a id="sec-5-3-3"></a>
#### 5.3.3. Functional Requirements

<a id="req-3-1"></a>
**REQ-3.1:** The system shall allow every case (ticket) to be assigned to a team member, regardless of which channel it originates from. [S22](#src-22)

- **Priority:** must
- **Stimulus/response:** SR-3.1
- **Origin:** registry RQ-0018 ([trace](#trace-req-3-1))

<a id="req-3-2"></a>
**REQ-3.2:** The system shall require a category for every case, such as replacement delivery, spare-part delivery, or refund. [S23](#src-23)

- **Priority:** must
- **Stimulus/response:** SR-3.1
- **Origin:** registry RQ-0019 ([trace](#trace-req-3-2))

<a id="req-3-3"></a>
**REQ-3.3:** The system shall support internal notes on each case so that all important information is visible at a glance. [S24](#src-24)

- **Priority:** must
- **Stimulus/response:** SR-3.1
- **Origin:** registry RQ-0020 ([trace](#trace-req-3-3))

<a id="req-3-4"></a>
**REQ-3.4:** The system shall support seamless handover so that any team member can continue a case started by another team member without gaps. [S25](#src-25)

- **Priority:** must
- **Stimulus/response:** SR-3.2
- **Origin:** registry RQ-0021 ([trace](#trace-req-3-4))

<a id="req-3-5"></a>
**REQ-3.5:** The system shall prioritize customer cases based on longest waiting time, regardless of topic. [S40](#src-40)

- **Priority:** should
- **Stimulus/response:** SR-3.3
- **Origin:** registry RQ-0022 ([trace](#trace-req-3-5))

<a id="req-3-6"></a>
**REQ-3.6:** The system shall route exceptions for refunds, replacements, and spare parts to Person C for handling or reassignment. [S33](#src-33)

- **Priority:** should
- **Stimulus/response:** SR-3.4
- **Origin:** registry RQ-0029 ([trace](#trace-req-3-6))

<a id="req-3-7"></a>
**REQ-3.7:** The system shall provide a single unified view per case displaying customer information, all communication channels, order details, tracking status, stock availability, warranty status, internal notes, and history. [S15](#src-15)

- **Priority:** should
- **Stimulus/response:** SR-3.1
- **Origin:** registry RQ-0030 ([trace](#trace-req-3-7))

<a id="sec-5-4"></a>
### 5.4. Service Level Monitoring

<a id="sec-5-4-1"></a>
#### 5.4.1. Description and Priority

Tracks response times and marketplace-specific reply deadlines to ensure compliance with service levels.

**Priority:** must

<a id="sec-5-4-2"></a>
#### 5.4.2. Stimulus/Response Sequences

**SR-4.1** [S27](#src-27) [S41](#src-41) [S42](#src-42) [S43](#src-43) [S44](#src-44)

- **Stimulus / condition:** An enquiry is received from a connected marketplace.
- **Response:** The system tracks customer reply deadlines (24 hours for Amazon, 48 hours for OTTO, eBay, and Kaufland, and the applicable reply deadline for Temu) and displays the waiting time and remaining reply deadline per marketplace in the overview.
- **Requirements:** [REQ-4.1](#req-4-1), [REQ-4.2](#req-4-2)

<a id="sec-5-4-3"></a>
#### 5.4.3. Functional Requirements

<a id="req-4-1"></a>
**REQ-4.1:** The system shall track customer reply deadlines for each marketplace, including 24 hours for Amazon, 48 hours for OTTO, eBay, and Kaufland, and the applicable reply deadline for Temu. [S27](#src-27) [S41](#src-41) [S42](#src-42) [S43](#src-43)

- **Priority:** must
- **Stimulus/response:** SR-4.1
- **Origin:** registry RQ-0023 ([trace](#trace-req-4-1))

<a id="req-4-2"></a>
**REQ-4.2:** The system shall display the waiting time and the remaining reply deadline per marketplace in the overview. [S44](#src-44)

- **Priority:** should
- **Stimulus/response:** SR-4.1
- **Origin:** registry RQ-0024 ([trace](#trace-req-4-2))

<a id="sec-5-5"></a>
### 5.5. Automated Messaging

<a id="sec-5-5-1"></a>
#### 5.5.1. Description and Priority

Dispatches automated acknowledgement messages to customers upon contact receipt.

**Priority:** should

<a id="sec-5-5-2"></a>
#### 5.5.2. Stimulus/Response Sequences

**SR-5.1** [S28](#src-28)

- **Stimulus / condition:** A message is received via WhatsApp or from the website contact form.
- **Response:** The system sends an automatic confirmation of receipt.
- **Requirements:** [REQ-5.1](#req-5-1)

<a id="sec-5-5-3"></a>
#### 5.5.3. Functional Requirements

<a id="req-5-1"></a>
**REQ-5.1:** Upon receiving a message via WhatsApp or from the website contact form, the system shall send an automatic confirmation of receipt. [S28](#src-28)

- **Priority:** should
- **Stimulus/response:** SR-5.1
- **Origin:** registry RQ-0025 ([trace](#trace-req-5-1))

<a id="sec-5-6"></a>
### 5.6. Workflow Automation

<a id="sec-5-6-1"></a>
#### 5.6.1. Description and Priority

Automates routine backend tasks including daily complaint reporting and fetching external logistics and inventory data.

**Priority:** should

<a id="sec-5-6-2"></a>
#### 5.6.2. Stimulus/Response Sequences

**SR-6.1** [S45](#src-45) [S29](#src-29)

- **Stimulus / condition:** Daily processing of replacement and spare-part deliveries is initiated.
- **Response:** The system generates a daily complaints list in CSV format containing customer name, address, reference, and items.
- **Requirements:** [REQ-6.1](#req-6-1)

**SR-6.2** [S46](#src-46) [S30](#src-30) [S31](#src-31)

- **Stimulus / condition:** Tracking or stock details are needed for a case.
- **Response:** The system retrieves tracking information from DPD and DHL logistics portals, and retrieves stock information from Warehouse System Z and Amazon Seller Central.
- **Requirements:** [REQ-6.2](#req-6-2), [REQ-6.3](#req-6-3)

<a id="sec-5-6-3"></a>
#### 5.6.3. Functional Requirements

<a id="req-6-1"></a>
**REQ-6.1:** The system shall generate a daily complaints list in CSV format containing customer name, address, reference, and items for replacement and spare-part deliveries. [S45](#src-45) [S29](#src-29)

- **Priority:** should
- **Stimulus/response:** SR-6.1
- **Origin:** registry RQ-0026 ([trace](#trace-req-6-1))

<a id="req-6-2"></a>
**REQ-6.2:** The system shall retrieve tracking information from DPD and DHL logistics portals. [S46](#src-46) [S30](#src-30)

- **Priority:** should
- **Stimulus/response:** SR-6.2
- **Origin:** registry RQ-0027 ([trace](#trace-req-6-2))

<a id="req-6-3"></a>
**REQ-6.3:** The system shall retrieve stock information from Warehouse System Z and Amazon Seller Central. [S46](#src-46) [S31](#src-31)

- **Priority:** should
- **Stimulus/response:** SR-6.2
- **Origin:** registry RQ-0028 ([trace](#trace-req-6-3))

<a id="sec-5-7"></a>
### 5.7. AI Assistance

<a id="sec-5-7-1"></a>
#### 5.7.1. Description and Priority

Assists agents with enquiry handling, drafting responses, summarizing long threads, and verifying order or stock status without autonomously approving financial or fulfillment actions.

**Priority:** should

<a id="sec-5-7-2"></a>
#### 5.7.2. Stimulus/Response Sequences

**SR-7.1** [S32](#src-32)

- **Stimulus / condition:** A decision regarding a refund, replacement, or similar action is pending.
- **Response:** The AI is restricted from making the decision, leaving the action to human support team members.
- **Requirements:** [REQ-7.1](#req-7-1)

**SR-7.2** [S47](#src-47) [S48](#src-48)

- **Stimulus / condition:** A customer submits a product or usage information enquiry, or requests general information such as invoice instructions.
- **Response:** The system provides AI assistance to answer the product and usage questions and provide the general instructions.
- **Requirements:** [REQ-7.2](#req-7-2), [REQ-7.3](#req-7-3)

**SR-7.3** [S49](#src-49) [S50](#src-50)

- **Stimulus / condition:** A case contains a long conversation or a draft reply needs refinement.
- **Response:** The system provides AI assistance to summarize the conversation and improve or rephrase draft replies based on standard templates.
- **Requirements:** [REQ-7.4](#req-7-4), [REQ-7.5](#req-7-5)

**SR-7.4** [S51](#src-51) [S52](#src-52) [S53](#src-53)

- **Stimulus / condition:** Validation of stock, warranty, or order details is requested.
- **Response:** The system provides AI assistance to check product/spare part stock, verify customer warranty period eligibility, and check order number validity.
- **Requirements:** [REQ-7.6](#req-7-6), [REQ-7.7](#req-7-7), [REQ-7.8](#req-7-8)

**SR-7.5** [S11](#src-11)

- **Stimulus / condition:** A response needs to be drafted for a customer enquiry.
- **Response:** The system prepares draft replies in German and English using approved product information, company policies, and relevant order details.
- **Requirements:** [REQ-7.9](#req-7-9)

<a id="sec-5-7-3"></a>
#### 5.7.3. Functional Requirements

<a id="req-7-1"></a>
**REQ-7.1:** The AI shall not make decisions about refunds, replacements, or similar actions. [S32](#src-32)

- **Priority:** should
- **Stimulus/response:** SR-7.1
- **Origin:** registry RQ-0031 ([trace](#trace-req-7-1))

<a id="req-7-2"></a>
**REQ-7.2:** The system shall provide AI assistance to answer customer product and usage information enquiries. [S47](#src-47)

- **Priority:** should
- **Stimulus/response:** SR-7.2
- **Origin:** registry RQ-0032 ([trace](#trace-req-7-2))

<a id="req-7-3"></a>
**REQ-7.3:** The system shall provide AI assistance to provide general information to customers, including instructions on how to obtain invoices. [S48](#src-48)

- **Priority:** should
- **Stimulus/response:** SR-7.2
- **Origin:** registry RQ-0033 ([trace](#trace-req-7-3))

<a id="req-7-4"></a>
**REQ-7.4:** The system shall provide AI assistance to summarize cases with long conversations. [S49](#src-49)

- **Priority:** should
- **Stimulus/response:** SR-7.3
- **Origin:** registry RQ-0034 ([trace](#trace-req-7-4))

<a id="req-7-5"></a>
**REQ-7.5:** The system shall provide AI assistance to improve and rephrase draft replies based on standard templates. [S50](#src-50)

- **Priority:** should
- **Stimulus/response:** SR-7.3
- **Origin:** registry RQ-0035 ([trace](#trace-req-7-5))

<a id="req-7-6"></a>
**REQ-7.6:** The system shall provide AI assistance to check whether a product or spare part is in stock. [S51](#src-51)

- **Priority:** should
- **Stimulus/response:** SR-7.4
- **Origin:** registry RQ-0036 ([trace](#trace-req-7-6))

<a id="req-7-7"></a>
**REQ-7.7:** The system shall provide AI assistance to verify whether a customer is within the warranty period. [S52](#src-52)

- **Priority:** should
- **Stimulus/response:** SR-7.4
- **Origin:** registry RQ-0037 ([trace](#trace-req-7-7))

<a id="req-7-8"></a>
**REQ-7.8:** The system shall provide AI assistance to check whether an order number is valid. [S53](#src-53)

- **Priority:** should
- **Stimulus/response:** SR-7.4
- **Origin:** registry RQ-0038 ([trace](#trace-req-7-8))

<a id="req-7-9"></a>
**REQ-7.9:** The system shall prepare draft replies in German and English using approved product information, company policies, and relevant order details. [S11](#src-11)

- **Priority:** could
- **Stimulus/response:** SR-7.5
- **Origin:** registry RQ-0039 ([trace](#trace-req-7-9))

<a id="sec-6"></a>
## 6. Nonfunctional Requirements

<a id="sec-6-1"></a>
### 6.1. Performance

> **Unresolved:** No approved performance requirements.

<a id="sec-6-2"></a>
### 6.2. Security

> **Unresolved:** No approved security requirements.

<a id="appendix-a"></a>
## Appendix A. Traceability Matrix

Every functional (REQ) and nonfunctional (NF) requirement, its approved registry requirement, and the source passages and quotes that support it. All rows were checked by `services.srs_validator` before export.

| SRS ID | Registry ID | Type | Priority | Source | Document, page | Stored quote |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="trace-req-1-1"></a>REQ-1.1 | RQ-0013 | functional | must | [S2](#src-2) DOC-bd148209ea0b-P0008 | source-document-1.pdf, p. 1 | “I propose building a dedicated customer-support platform for Company A, bringing eBay, OTTO, and Kaufland support into one workspace.” |
| REQ-1.1 | RQ-0013 | functional | must | [S17](#src-17) DOC-bd148209ea0b-P0063 | source-document-1.pdf, p. 3 | “We currently have eBay, Kaufland, OTTO, Amazon EU, Amazon US and two email addresses connected.” |
| <a id="trace-req-1-2"></a>REQ-1.2 | RQ-0014 | functional | should | [S18](#src-18) DOC-bd148209ea0b-P0064 | source-document-1.pdf, p. 3 | “- WhatsApp (connecting it has turned out to be difficult)” |
| <a id="trace-req-1-3"></a>REQ-1.3 | RQ-0015 | functional | should | [S19](#src-19) DOC-bd148209ea0b-P0065 | source-document-1.pdf, p. 3 | “- Temu (not connected at all)” |
| <a id="trace-req-1-4"></a>REQ-1.4 | RQ-0016 | functional | should | [S20](#src-20) DOC-bd148209ea0b-P0066 | source-document-1.pdf, p. 3 | “- Our new AI phone agent (Phone Agent Y). We can handle these cases in Helpdesk Tool X, but it takes a lot of time.” |
| <a id="trace-req-2-1"></a>REQ-2.1 | RQ-0017 | functional | must | [S21](#src-21) DOC-bd148209ea0b-P0068 | source-document-1.pdf, p. 3 | “Recognising that the same customer has contacted us through several channels (e.g. Amazon and email).” |
| REQ-2.1 | RQ-0017 | functional | must | [S26](#src-26) DOC-bd148209ea0b-P0077 | source-document-1.pdf, p. 3 | “- Automatic detection and linking of the same customer across channels (very important)” |
| <a id="trace-req-3-1"></a>REQ-3.1 | RQ-0018 | functional | must | [S22](#src-22) DOC-bd148209ea0b-P0073 | source-document-1.pdf, p. 3 | “- Every case (ticket) must be assignable to a team member, no matter which channel it comes from.” |
| <a id="trace-req-3-2"></a>REQ-3.2 | RQ-0019 | functional | must | [S23](#src-23) DOC-bd148209ea0b-P0074 | source-document-1.pdf, p. 3 | “- Every case must have a category (e.g. replacement delivery, spare-part delivery, refund).” |
| <a id="trace-req-3-3"></a>REQ-3.3 | RQ-0020 | functional | must | [S24](#src-24) DOC-bd148209ea0b-P0075 | source-document-1.pdf, p. 3 | “- Internal notes on each case, so all important information is visible at a glance” |
| <a id="trace-req-3-4"></a>REQ-3.4 | RQ-0021 | functional | must | [S25](#src-25) DOC-bd148209ea0b-P0076 | source-document-1.pdf, p. 3 | “- Seamless handover: if Person D starts a case, Person E or I must be able to continue it without any gaps.” |
| <a id="trace-req-3-5"></a>REQ-3.5 | RQ-0022 | functional | should | [S40](#src-40) DOC-bd148209ea0b-P0091 | source-document-1.pdf, p. 4 | “We always handle the case that has been waiting longest, regardless of topic.” |
| <a id="trace-req-3-6"></a>REQ-3.6 | RQ-0029 | functional | should | [S33](#src-33) DOC-bd148209ea0b-P0114 | source-document-1.pdf, p. 5 | “Exceptions come to me. I either handle them myself or decide who takes them over.” |
| <a id="trace-req-3-7"></a>REQ-3.7 | RQ-0030 | functional | should | [S15](#src-15) DOC-bd148209ea0b-P0121 | source-document-1.pdf, p. 5 | “ideally one view per case that shows everything we need (customer, all channels, order, tracking, stock, warranty, notes, history).” |
| <a id="trace-req-4-1"></a>REQ-4.1 | RQ-0023 | functional | must | [S27](#src-27) DOC-bd148209ea0b-P0092 | source-document-1.pdf, p. 4 | “each marketplace sets a deadline for replying to the customer. The case doesn't have to be solved by then, but the customer must at least receive a reply:” |
| REQ-4.1 | RQ-0023 | functional | must | [S41](#src-41) DOC-bd148209ea0b-P0093 | source-document-1.pdf, p. 4 | “- Amazon: 24 hours” |
| REQ-4.1 | RQ-0023 | functional | must | [S42](#src-42) DOC-bd148209ea0b-P0094 | source-document-1.pdf, p. 4 | “- OTTO, eBay, Kaufland: 48 hours” |
| REQ-4.1 | RQ-0023 | functional | must | [S43](#src-43) DOC-bd148209ea0b-P0095 | source-document-1.pdf, p. 4 | “- Temu: there is a deadline as well, I still need to check the exact time.” |
| <a id="trace-req-4-2"></a>REQ-4.2 | RQ-0024 | functional | should | [S44](#src-44) DOC-bd148209ea0b-P0097 | source-document-1.pdf, p. 4 | “Important for the platform: the waiting time and the remaining deadline per marketplace should be clearly visible in the overview.” |
| <a id="trace-req-5-1"></a>REQ-5.1 | RQ-0025 | functional | should | [S28](#src-28) DOC-bd148209ea0b-P0096 | source-document-1.pdf, p. 4 | “For WhatsApp and for messages from the contact form on our website (these arrive in one of our email inboxes), we send an automatic confirmation of receipt.” |
| <a id="trace-req-6-1"></a>REQ-6.1 | RQ-0026 | functional | should | [S45](#src-45) DOC-bd148209ea0b-P0070 | source-document-1.pdf, p. 3 | “- Keeping the daily complaints list (CSV) and uploading it to Asana for the warehouse” |
| REQ-6.1 | RQ-0026 | functional | should | [S29](#src-29) DOC-bd148209ea0b-P0099 | source-document-1.pdf, p. 4 | “- Replacement and spare-part deliveries: documented daily in a complaints list (CSV with name, address, reference and items). At the end of the day the list is uploaded to Asana as an order, and the warehouse team processes it.” |
| <a id="trace-req-6-2"></a>REQ-6.2 | RQ-0027 | functional | should | [S46](#src-46) DOC-bd148209ea0b-P0071 | source-document-1.pdf, p. 3 | “- Checking stock and tracking in several different systems” |
| REQ-6.2 | RQ-0027 | functional | should | [S30](#src-30) DOC-bd148209ea0b-P0100 | source-document-1.pdf, p. 5 | “- Tracking: DPD and DHL logistics portals” |
| <a id="trace-req-6-3"></a>REQ-6.3 | RQ-0028 | functional | should | [S46](#src-46) DOC-bd148209ea0b-P0071 | source-document-1.pdf, p. 3 | “- Checking stock and tracking in several different systems” |
| REQ-6.3 | RQ-0028 | functional | should | [S31](#src-31) DOC-bd148209ea0b-P0101 | source-document-1.pdf, p. 5 | “- Stock: Warehouse System Z (our warehouse in City A) and Amazon Seller Central (Amazon stock).” |
| <a id="trace-req-7-1"></a>REQ-7.1 | RQ-0031 | functional | should | [S32](#src-32) DOC-bd148209ea0b-P0106 | source-document-1.pdf, p. 5 | “The AI should not make any decisions about refunds, replacements or similar actions.” |
| <a id="trace-req-7-2"></a>REQ-7.2 | RQ-0032 | functional | should | [S47](#src-47) DOC-bd148209ea0b-P0107 | source-document-1.pdf, p. 5 | “- Product and usage information” |
| <a id="trace-req-7-3"></a>REQ-7.3 | RQ-0033 | functional | should | [S48](#src-48) DOC-bd148209ea0b-P0108 | source-document-1.pdf, p. 5 | “- General information (e.g. how a customer can get their invoice)” |
| <a id="trace-req-7-4"></a>REQ-7.4 | RQ-0034 | functional | should | [S49](#src-49) DOC-bd148209ea0b-P0109 | source-document-1.pdf, p. 5 | “- Summarising cases with long conversations (Helpdesk Tool X already does this)” |
| <a id="trace-req-7-5"></a>REQ-7.5 | RQ-0035 | functional | should | [S50](#src-50) DOC-bd148209ea0b-P0110 | source-document-1.pdf, p. 5 | “- Improving and rephrasing replies. We have standard templates, but sometimes need help with wording.” |
| <a id="trace-req-7-6"></a>REQ-7.6 | RQ-0036 | functional | should | [S51](#src-51) DOC-bd148209ea0b-P0111 | source-document-1.pdf, p. 5 | “- Checking whether a product or spare part is in stock” |
| <a id="trace-req-7-7"></a>REQ-7.7 | RQ-0037 | functional | should | [S52](#src-52) DOC-bd148209ea0b-P0112 | source-document-1.pdf, p. 5 | “- Checking whether the customer is still within the warranty period” |
| <a id="trace-req-7-8"></a>REQ-7.8 | RQ-0038 | functional | should | [S53](#src-53) DOC-bd148209ea0b-P0113 | source-document-1.pdf, p. 5 | “- Checking whether an order number is valid (I'm currently trying to set this up in our Phone Agent Y phone agent)” |
| <a id="trace-req-7-9"></a>REQ-7.9 | RQ-0039 | functional | could | [S11](#src-11) DOC-bd148209ea0b-P0012 | source-document-1.pdf, p. 1 | “prepare German/English replies using approved product information, company policies, and relevant order details.” |

<a id="appendix-b"></a>
## Appendix B. Sources

<a id="src-1"></a>
**[S1]** `DOC-bd148209ea0b-P0002` · source-document-1.pdf, page 1

> Proposal: AI-Powered Customer Support Platform

<a id="src-2"></a>
**[S2]** `DOC-bd148209ea0b-P0008` · source-document-1.pdf, page 1

> After discussion with Person B, I propose building a dedicated customer-support platform for Company A, bringing eBay, OTTO, and Kaufland support into one workspace.

<a id="src-3"></a>
**[S3]** `DOC-bd148209ea0b-P0004` · source-document-1.pdf, page 1

> Person A &lt;person.a@company-a.invalid&gt; [date redacted]

<a id="src-4"></a>
**[S4]** `DOC-bd148209ea0b-P0006` · source-document-1.pdf, page 1

> Cc: Person B | Company A &lt;person.b@company-a.invalid&gt;

<a id="src-5"></a>
**[S5]** `DOC-bd148209ea0b-P0007` · source-document-1.pdf, page 1

> Hello Person C,

<a id="src-6"></a>
**[S6]** `DOC-bd148209ea0b-P0087` · source-document-1.pdf, page 4

> - Person D: 5 days a week (working from Country A)

<a id="src-7"></a>
**[S7]** `DOC-bd148209ea0b-P0088` · source-document-1.pdf, page 4

> - Person E: Mon, Tue and Thu, half days

<a id="src-8"></a>
**[S8]** `DOC-bd148209ea0b-P0089` · source-document-1.pdf, page 4

> - Me: every day as needed, mainly everything that can't wait, plus exceptions

<a id="src-9"></a>
**[S9]** `DOC-bd148209ea0b-P0010` · source-document-1.pdf, page 1

> The solution would combine:

<a id="src-10"></a>
**[S10]** `DOC-bd148209ea0b-P0011` · source-document-1.pdf, page 1

> A shared inbox: Manage customer conversations, assignments, internal notes, attachments, and response deadlines across the three marketplaces.

<a id="src-11"></a>
**[S11]** `DOC-bd148209ea0b-P0012` · source-document-1.pdf, page 1

> AI assistance: Summarize conversations, identify enquiry types, and prepare German/English replies using approved product information, company policies, and relevant order details.

<a id="src-12"></a>
**[S12]** `DOC-bd148209ea0b-P0013` · source-document-1.pdf, page 1

> Workflow automation: Retrieve order and tracking information, create follow-up tasks, and guide agents through delivery enquiries, returns, and damaged-item cases.

<a id="src-13"></a>
**[S13]** `DOC-bd148209ea0b-P0014` · source-document-1.pdf, page 1

> Controlled actions and reporting: Require appropriate approval for refunds, cancellations, and replacements, while tracking case progress, response times, and recurring customer issues.

<a id="src-14"></a>
**[S14]** `DOC-bd148209ea0b-P0015` · source-document-1.pdf, page 1

> The goal is to reduce repetitive work and help the team resolve enquiries more quickly and consistently. We would validate the available API and email connection methods for each marketplace before finalizing the integration design.

<a id="src-15"></a>
**[S15]** `DOC-bd148209ea0b-P0121` · source-document-1.pdf, page 5

> For us it's all connected: faster replies lead to shorter handling times. But we can only reply faster with a good overview, ideally one view per case that shows everything we need (customer, all channels, order, tracking, stock, warranty, notes, history).

<a id="src-16"></a>
**[S16]** `DOC-bd148209ea0b-P0017` · source-document-1.pdf, page 1

> 1. Current problems: What specifically prevents the Helpdesk Tool X integrations from working, and which manual tasks consume the most time?

<a id="src-17"></a>
**[S17]** `DOC-bd148209ea0b-P0063` · source-document-1.pdf, page 3

> The existing Helpdesk Tool X integrations work fine. We currently have eBay, Kaufland, OTTO, Amazon EU, Amazon US and two email addresses connected. What's still missing:

<a id="src-18"></a>
**[S18]** `DOC-bd148209ea0b-P0064` · source-document-1.pdf, page 3

> - WhatsApp (connecting it has turned out to be difficult)

<a id="src-19"></a>
**[S19]** `DOC-bd148209ea0b-P0065` · source-document-1.pdf, page 3

> - Temu (not connected at all)

<a id="src-20"></a>
**[S20]** `DOC-bd148209ea0b-P0066` · source-document-1.pdf, page 3

> - Our new AI phone agent (Phone Agent Y). We can handle these cases in Helpdesk Tool X, but it takes a lot of time.

<a id="src-21"></a>
**[S21]** `DOC-bd148209ea0b-P0068` · source-document-1.pdf, page 3

> - Recognising that the same customer has contacted us through several channels (e.g. Amazon and email). We often don't notice, and sometimes two agents end up working on the same customer.

<a id="src-22"></a>
**[S22]** `DOC-bd148209ea0b-P0073` · source-document-1.pdf, page 3

> - Every case (ticket) must be assignable to a team member, no matter which channel it comes from.

<a id="src-23"></a>
**[S23]** `DOC-bd148209ea0b-P0074` · source-document-1.pdf, page 3

> - Every case must have a category (e.g. replacement delivery, spare-part delivery, refund).

<a id="src-24"></a>
**[S24]** `DOC-bd148209ea0b-P0075` · source-document-1.pdf, page 3

> - Internal notes on each case, so all important information is visible at a glance

<a id="src-25"></a>
**[S25]** `DOC-bd148209ea0b-P0076` · source-document-1.pdf, page 3

> - Seamless handover: if Person D starts a case, Person E or I must be able to continue it without any gaps.

<a id="src-26"></a>
**[S26]** `DOC-bd148209ea0b-P0077` · source-document-1.pdf, page 3

> - Automatic detection and linking of the same customer across channels (very important)

<a id="src-27"></a>
**[S27]** `DOC-bd148209ea0b-P0092` · source-document-1.pdf, page 4

> On top of that, each marketplace sets a deadline for replying to the customer. The case doesn't have to be solved by then, but the customer must at least receive a reply:

<a id="src-28"></a>
**[S28]** `DOC-bd148209ea0b-P0096` · source-document-1.pdf, page 4

> For WhatsApp and for messages from the contact form on our website (these arrive in one of our email inboxes), we send an automatic confirmation of receipt.

<a id="src-29"></a>
**[S29]** `DOC-bd148209ea0b-P0099` · source-document-1.pdf, page 4

> - Replacement and spare-part deliveries: documented daily in a complaints list (CSV with name, address, reference and items). At the end of the day the list is uploaded to Asana as an order, and the warehouse team processes it.

<a id="src-30"></a>
**[S30]** `DOC-bd148209ea0b-P0100` · source-document-1.pdf, page 5

> - Tracking: DPD and DHL logistics portals

<a id="src-31"></a>
**[S31]** `DOC-bd148209ea0b-P0101` · source-document-1.pdf, page 5

> - Stock: Warehouse System Z (our warehouse in City A) and Amazon Seller Central (Amazon stock). A new tool for the other marketplaces is in progress. As far as I know, Person F is working on it.

<a id="src-32"></a>
**[S32]** `DOC-bd148209ea0b-P0106` · source-document-1.pdf, page 5

> The AI should not make any decisions about refunds, replacements or similar actions. It would be useful for:

<a id="src-33"></a>
**[S33]** `DOC-bd148209ea0b-P0114` · source-document-1.pdf, page 5

> My team decides on refunds, replacements and spare parts. Exceptions come to me. I either handle them myself or decide who takes them over.

<a id="src-34"></a>
**[S34]** `DOC-bd148209ea0b-P0086` · source-document-1.pdf, page 4

> Team:

<a id="src-35"></a>
**[S35]** `DOC-bd148209ea0b-P0116` · source-document-1.pdf, page 5

> - We have response templates and policies, but they need constant updates because we always have to follow each marketplace's rules.

<a id="src-36"></a>
**[S36]** `DOC-bd148209ea0b-P0085` · source-document-1.pdf, page 4

> Support is closed on weekends and public holidays. That's why Mondays and the days after public holidays or long weekends are much busier. We are often already 1–2 days behind on Friday, and on Monday even more comes in.

<a id="src-37"></a>
**[S37]** `DOC-bd148209ea0b-P0117` · source-document-1.pdf, page 5

> - Product manuals for every product are stored in Dropbox.

<a id="src-38"></a>
**[S38]** `DOC-bd148209ea0b-P0118` · source-document-1.pdf, page 5

> - Helpdesk Tool X keeps all conversations for 24 months. We also have several years of emails in our mail accounts.

<a id="src-39"></a>
**[S39]** `DOC-bd148209ea0b-P0119` · source-document-1.pdf, page 5

> - I'm building a knowledge base with Claude and Obsidian. It's still at an early stage and more of a future idea.

<a id="src-40"></a>
**[S40]** `DOC-bd148209ea0b-P0091` · source-document-1.pdf, page 4

> We always handle the case that has been waiting longest, regardless of topic.

<a id="src-41"></a>
**[S41]** `DOC-bd148209ea0b-P0093` · source-document-1.pdf, page 4

> - Amazon: 24 hours

<a id="src-42"></a>
**[S42]** `DOC-bd148209ea0b-P0094` · source-document-1.pdf, page 4

> - OTTO, eBay, Kaufland: 48 hours

<a id="src-43"></a>
**[S43]** `DOC-bd148209ea0b-P0095` · source-document-1.pdf, page 4

> - Temu: there is a deadline as well, I still need to check the exact time.

<a id="src-44"></a>
**[S44]** `DOC-bd148209ea0b-P0097` · source-document-1.pdf, page 4

> Important for the platform: the waiting time and the remaining deadline per marketplace should be clearly visible in the overview.

<a id="src-45"></a>
**[S45]** `DOC-bd148209ea0b-P0070` · source-document-1.pdf, page 3

> - Keeping the daily complaints list (CSV) and uploading it to Asana for the warehouse

<a id="src-46"></a>
**[S46]** `DOC-bd148209ea0b-P0071` · source-document-1.pdf, page 3

> - Checking stock and tracking in several different systems

<a id="src-47"></a>
**[S47]** `DOC-bd148209ea0b-P0107` · source-document-1.pdf, page 5

> - Product and usage information

<a id="src-48"></a>
**[S48]** `DOC-bd148209ea0b-P0108` · source-document-1.pdf, page 5

> - General information (e.g. how a customer can get their invoice)

<a id="src-49"></a>
**[S49]** `DOC-bd148209ea0b-P0109` · source-document-1.pdf, page 5

> - Summarising cases with long conversations (Helpdesk Tool X already does this)

<a id="src-50"></a>
**[S50]** `DOC-bd148209ea0b-P0110` · source-document-1.pdf, page 5

> - Improving and rephrasing replies. We have standard templates, but sometimes need help with wording.

<a id="src-51"></a>
**[S51]** `DOC-bd148209ea0b-P0111` · source-document-1.pdf, page 5

> - Checking whether a product or spare part is in stock

<a id="src-52"></a>
**[S52]** `DOC-bd148209ea0b-P0112` · source-document-1.pdf, page 5

> - Checking whether the customer is still within the warranty period

<a id="src-53"></a>
**[S53]** `DOC-bd148209ea0b-P0113` · source-document-1.pdf, page 5

> - Checking whether an order number is valid (I'm currently trying to set this up in our Phone Agent Y phone agent)

<a id="appendix-c"></a>
## Appendix C. Validation Notes

- 27 of 27 requirements traced to approved registry requirements with valid evidence; 53 source passages cited.
- Warning: overall\_description.operating\_environment is unresolved: The source passages do not state the operating environment, hosting infrastructure, or supported client platforms.
- Warning: overall\_description.user\_documentation is unresolved: The source passages do not specify user documentation requirements or user manuals to be delivered.
- Warning: external\_interface\_requirements.hardware\_interfaces is not\_applicable: The source passages do not specify any hardware interfaces for the software platform.
- Warning: 6.1 Performance is unresolved: No approved performance requirements.
- Warning: 6.2 Security is unresolved: No approved security requirements.
